"""Découverte élargie de prospects via Google Places (Text Search).

Objectif métier : élargir le SOURCING en haut de funnel — balayer plusieurs
mots-clés et plusieurs zones (quartiers/villes) en une passe — sans dégrader le
tri. La qualité reste garantie en AVAL (filtres éliminatoires + BPS) : on ne
relâche aucun critère, on alimente seulement davantage le tri.

Trois leviers, tous déterministes et configurables :
  1. multi-keywords × multi-zones : produit cartésien des variantes FOURNIES par
     l'opérateur (aucune liste de synonymes/quartiers auto-générée -> zéro dérive
     de pertinence, zéro table à maintenir) ;
  2. pagination Google : jusqu'à MAX_PAGES_PER_QUERY pages par requête (Google
     plafonne à ~60 résultats/requête via next_page_token), pour DÉPASSER le
     plafond historique de 20 et atteindre DISCOVERY_CAP sur un marché dense ;
  3. déduplication par place_id : un même établissement remonté par plusieurs
     requêtes n'est compté qu'une fois.

Bornes de coût API explicites : DISCOVERY_CAP (résultats agrégés) et MAX_QUERIES
(nombre de requêtes Text Search) — un balayage ne peut pas exploser le budget.

I/O isolée derrière `fetch` (params -> JSON Google) et `sleep`, tous deux
injectables -> testable sans réseau et sans attente réelle.
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# Plafond global de résultats AGRÉGÉS (après dédup), tous keywords/zones confondus.
# "marché dense" : 50 remonte largement la longue traîne là où 20 plafonnait, tout
# en bornant le coût aval (chaque résultat -> 1 get_place_details). Configurable.
DISCOVERY_CAP = 50
# Pages suivies par requête. Google Text Search renvoie 20 résultats/page et
# plafonne à 3 pages (~60) via next_page_token : au-delà, le token n'existe plus.
MAX_PAGES_PER_QUERY = 3
# Délai (s) avant qu'un next_page_token devienne valide côté Google (contrainte
# de l'API, pas un throttle arbitraire). Injectable (tests -> no-op).
NEXT_PAGE_DELAY = 2.0
# Garde-fou de coût : nombre maximum de requêtes Text Search distinctes lancées
# (borne keywords × zones). Le cap DISCOVERY_CAP arrête en général bien avant.
MAX_QUERIES = 25

# Métadonnée interne ajoutée aux résultats Google. Elle reste hors du modèle
# Prospect et n'est donc jamais écrite dans Notion.
SOURCE_CONTEXT_KEY = "_discovery_sources"


def _as_list(value):
    """Normalise une entrée en liste de termes nettoyés.

    Accepte une chaîne CSV ("coiffeur, barbier" -> ["coiffeur", "barbier"]),
    une liste, ou None. Rétrocompatible : une valeur simple ("coiffeur") ->
    liste d'un élément. Les éléments vides sont écartés.
    """
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    return [str(v).strip() for v in items if str(v).strip()]


def _query_contexts(keywords, cities):
    """Requêtes dédupliquées avec leur keyword et leur zone sources."""
    contexts = []
    seen = set()
    for keyword in _as_list(keywords):
        for zone in _as_list(cities):
            query = f"{keyword} {zone}".strip()
            if query and query not in seen:
                seen.add(query)
                contexts.append({"query": query, "keyword": keyword, "zone": zone})
    return contexts


def build_queries(keywords, cities):
    """Produit cartésien des requêtes "keyword zone", ordre déterministe et dédup.

    Ordre keyword-majeur : toutes les zones du 1er mot-clé, puis du 2e, etc. Les
    requêtes en doublon (ex. mêmes termes saisis deux fois) sont fusionnées en
    conservant l'ordre d'apparition.
    """
    return [context["query"] for context in _query_contexts(keywords, cities)]


def _default_fetch(params):
    """Getter HTTP par défaut : renvoie le JSON Google brut (clé lue à l'appel)."""
    full = dict(params)
    full.setdefault("key", os.getenv("GOOGLE_API_KEY"))
    resp = requests.get(_TEXTSEARCH_URL, params=full, timeout=10)
    return resp.json()


def _add_source_context(place, *, keyword, zone):
    """Ajoute une source de découverte sans doublon à une place interne."""
    source = {"keyword": keyword, "zone": zone}
    sources = place.setdefault(SOURCE_CONTEXT_KEY, [])
    if source not in sources:
        sources.append(source)


def _collect_query(query, *, keyword, zone, fetch, sleep, seen, by_place_id,
                   results, stats, max_results, max_pages):
    """Pagine UNE requête, ajoute les résultats inédits à `results` (dédup `seen`).

    S'arrête dès que `max_results` est atteint ou qu'il n'y a plus de page. Une
    erreur API (status hors OK/ZERO_RESULTS) interrompt CETTE requête seulement
    (non bloquant : les autres requêtes du balayage continuent).
    """
    params = {"query": query, "language": "fr"}
    for _ in range(max_pages):
        if len(results) >= max_results:
            return
        try:
            data = fetch(params)
        except Exception as exc:
            stats["errors"] += 1
            print(f"[Google Places erreur] query='{query}' réseau={type(exc).__name__} "
                  f"— {exc}")
            return
        stats["pages_fetched"] += 1
        if not isinstance(data, dict):
            stats["errors"] += 1
            print(f"[Google Places erreur] query='{query}' réponse invalide")
            return
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            stats["errors"] += 1
            print(f"[Google Places erreur] query='{query}' status={status} — "
                  f"{data.get('error_message', '')}")
            return
        raw_places = data.get("results", [])
        if not isinstance(raw_places, list):
            raw_places = []
        stats["raw_results"] += len(raw_places)
        for place in raw_places:
            if not isinstance(place, dict):
                continue
            pid = place.get("place_id", "")
            if pid and pid in seen:
                stats["duplicates"] += 1
                _add_source_context(by_place_id[pid], keyword=keyword, zone=zone)
                continue  # même établissement déjà collecté ailleurs
            collected = dict(place)
            _add_source_context(collected, keyword=keyword, zone=zone)
            if pid:
                seen.add(pid)
                by_place_id[pid] = collected
            results.append(collected)
            if len(results) >= max_results:
                return
        next_token = data.get("next_page_token")
        if not next_token:
            return
        sleep(NEXT_PAGE_DELAY)  # le token n'est valide qu'après un court délai
        params = {"pagetoken": next_token}


def search_places(keywords, cities, *, fetch=None, sleep=None, stats=None,
                  max_results=DISCOVERY_CAP, max_pages=MAX_PAGES_PER_QUERY,
                  max_queries=MAX_QUERIES):
    """Découverte élargie : balaye keywords × zones, pagine, dédup, plafonne.

    `keywords` / `cities` : chaîne CSV ou liste (rétrocompatible avec un appel
    simple `search_places("coiffeur", "Lyon")` -> une requête paginée).

    Retourne une liste de places Google (dicts), uniques par place_id, dans
    l'ordre de découverte, plafonnée à `max_results`. `fetch` (params -> JSON) et
    `sleep` (secondes -> None) sont injectables pour les tests (aucun réseau).
    """
    do_fetch = fetch or _default_fetch
    do_sleep = sleep if sleep is not None else time.sleep

    keyword_list = _as_list(keywords)
    zone_list = _as_list(cities)
    contexts = _query_contexts(keyword_list, zone_list)
    run_stats = stats if stats is not None else {}
    run_stats.clear()
    run_stats.update({
        "keyword_count": len(keyword_list),
        "zone_count": len(zone_list),
        "queries_generated": len(contexts),
        "queries_executed": 0,
        "pages_fetched": 0,
        "raw_results": 0,
        "deduped_results": 0,
        "duplicates": 0,
        "errors": 0,
    })
    if len(contexts) > max_queries:
        print(f"[découverte] {len(contexts)} requêtes demandées -> bornées à "
              f"{max_queries} (garde-fou coût API).")
        contexts = contexts[:max_queries]

    seen = set()
    by_place_id = {}
    results = []
    for context in contexts:
        if len(results) >= max_results:
            break
        run_stats["queries_executed"] += 1
        _collect_query(
            context["query"], keyword=context["keyword"], zone=context["zone"],
            fetch=do_fetch, sleep=do_sleep, seen=seen, by_place_id=by_place_id,
            results=results, stats=run_stats, max_results=max_results,
            max_pages=max_pages,
        )
    run_stats["deduped_results"] = len(results)
    return results[:max_results]
