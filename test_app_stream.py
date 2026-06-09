"""Tests du serveur / câblage du flux SSE (app.py).

Runner autonome, SANS réseau ni clé API : la recherche Google est remplacée
par un fake, et les coroutines de route sont appelées en direct via asyncio.
Aucun appel Google / Anthropic / Notion, aucune écriture CRM.

  python3 test_app_stream.py   (code 1 si au moins un test échoue)

Vérifie que le bouton « Lancer » a réellement de quoi se connecter :
  - la route / sert le HTML de l'app via un chemin ABSOLU (lançable depuis
    n'importe quel dossier) ;
  - /health répond et signale les clés manquantes ;
  - run_pipeline émet bien la séquence d'événements attendue ;
  - une configuration incomplète remonte un événement 'error' clair (pas un
    silencieux « 0 résultat »).
"""

import asyncio
import contextlib
import io
import queue
import sys

import app

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


_CACHE_FUNCS = ("load_cache", "save_cache", "was_processed_recently", "mark_processed")


def _neutralize_cache():
    """Coupe toute I/O de cache : rien n'est lu/écrit, aucun Place ID « récent ».

    Retourne le dict des fonctions d'origine (à restaurer par l'appelant).
    """
    saved = {k: getattr(app, k) for k in _CACHE_FUNCS}
    app.load_cache = lambda *a, **k: {}
    app.save_cache = lambda *a, **k: True
    app.was_processed_recently = lambda *a, **k: False
    app.mark_processed = lambda *a, **k: None
    return saved


def _drain(keyword, city):
    """Exécute run_pipeline en direct (synchrone), cache neutralisé, et collecte
    les événements (aucun fichier .vitryne_cache.json touché pendant les tests)."""
    q = queue.Queue()
    saved = _neutralize_cache()
    try:
        app.run_pipeline(keyword, city, q)
    finally:
        for k, v in saved.items():
            setattr(app, k, v)
    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)
    return events


def _empty_search_stats(keywords, cities):
    return {
        "keyword_count": len(app.discovery._as_list(keywords)),
        "zone_count": len(app.discovery._as_list(cities)),
        "queries_generated": len(app.discovery.build_queries(keywords, cities)),
        "queries_executed": 0,
        "pages_fetched": 0,
        "raw_results": 0,
        "deduped_results": 0,
        "duplicates": 0,
        "errors": 0,
    }


def _fake_empty_search(calls=None):
    def fake(keywords, cities, *, stats=None):
        if calls is not None:
            calls.append((keywords, cities))
        if stats is not None:
            stats.update(_empty_search_stats(keywords, cities))
        return []

    return fake


# --- Route / : chemin absolu + sert bien l'app -------------------------------


def test_index_route_absolute_and_serves_app():
    print("test_index_route_absolute_and_serves_app")
    check(app.INDEX_HTML.is_absolute(), "INDEX_HTML est un chemin absolu (lançable hors racine)")
    check(app.INDEX_HTML.exists(), "le fichier index.html existe")
    resp = asyncio.run(app.index())
    check(resp.status_code == 200, "/ répond 200")
    body = resp.body.decode("utf-8")
    check('id="app"' in body, "/ sert bien le HTML de l'interface")
    check("/stream?keyword=" in body, "le HTML servi pointe vers /stream (bouton connecté)")
    check('id="launch-btn"' in body and 'onclick="launch()"' in body,
          "le bouton Lancer appelle launch()")
    check("async function launch()" in body and "fetch(`/stream?keyword=" in body,
          "launch() déclenche bien le flux scraper /stream")


# --- /health -----------------------------------------------------------------


def test_health():
    print("test_health")
    res = asyncio.run(app.health())
    check(res.get("status") == "ok", "/health renvoie status ok")
    check(isinstance(res.get("missing_keys"), list), "/health expose la liste des clés manquantes")


# --- Flux SSE : séquence d'événements ----------------------------------------


def test_stream_flow_no_network():
    print("test_stream_flow_no_network")
    saved = app.discovery.search_places
    app.discovery.search_places = _fake_empty_search()  # aucun résultat -> aucun appel aval
    try:
        events = _drain("kine", "Lyon")
    finally:
        app.discovery.search_places = saved
    types = [e["type"] for e in events]
    check(types == ["searching", "places_found", "done"],
          f"séquence attendue searching->places_found->done (obtenu : {types})")
    check(events[1]["type"] == "places_found" and events[1]["count"] == 0,
          "places_found count=0")
    done = events[-1]
    check(done["type"] == "done", "dernier événement = done")
    check(done["total"] == 0 and done["inserted"] == 0 and done["skipped"] == 0
          and done["eliminated"] == 0 and done["cached"] == 0,
          "done : compteurs à 0 (dont cached)")
    check(done["top"] == [], "done : top vide (aucun prospect)")
    check(done["stats"]["total"] == 0, "done : stats.total = 0")


# --- CSV app -> discovery + thread SSE déclenché par /stream -----------------


def test_app_csv_inputs_reach_discovery():
    print("test_app_csv_inputs_reach_discovery")
    cases = (
        ("kine", "Lyon", 1, 1, 1),
        ("kine, osteopathe", "Lyon", 2, 1, 2),
        ("kine", "Lyon, Villeurbanne", 1, 2, 2),
        ("kine, osteopathe", "Lyon, Villeurbanne", 2, 2, 4),
    )
    calls = []
    saved_search = app.discovery.search_places
    saved_keys = app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"
    app.discovery.search_places = _fake_empty_search(calls)
    try:
        for keywords, zones, keyword_count, zone_count, query_count in cases:
            events = _drain(keywords, zones)
            found = next(e for e in events if e["type"] == "places_found")
            check(found["keyword_count"] == keyword_count,
                  f"app : '{keywords}' -> {keyword_count} keyword(s)")
            check(found["zone_count"] == zone_count,
                  f"app : '{zones}' -> {zone_count} zone(s)")
            check(found["query_count"] == query_count,
                  f"app : produit CSV -> {query_count} requête(s)")
    finally:
        app.discovery.search_places = saved_search
        app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN = saved_keys
    check(calls == [(case[0], case[1]) for case in cases],
          "run_pipeline appelle discovery.search_places avec les saisies opérateur intactes")


def test_event_generator_launches_pipeline():
    print("test_event_generator_launches_pipeline")
    calls = []
    saved = app.run_pipeline

    def fake_pipeline(keyword, city, q):
        calls.append((keyword, city))
        q.put({"type": "done"})
        q.put(None)

    async def collect():
        return [chunk async for chunk in app.event_generator("kine", "Lyon")]

    app.run_pipeline = fake_pipeline
    try:
        chunks = asyncio.run(collect())
    finally:
        app.run_pipeline = saved
    check(calls == [("kine", "Lyon")],
          "flux lancé par le bouton : event_generator déclenche run_pipeline")
    check(any('"type": "done"' in chunk for chunk in chunks),
          "flux lancé par le bouton : événement pipeline retransmis en SSE")


def test_discovery_logs():
    print("test_discovery_logs")
    saved_search = app.discovery.search_places
    saved_keys = app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"

    def fake_search(keywords, cities, *, stats=None):
        stats.update({
            "keyword_count": 2, "zone_count": 3,
            "queries_generated": 6, "queries_executed": 4,
            "raw_results": 12, "deduped_results": 0,
        })
        return []

    app.discovery.search_places = fake_search
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _drain("kine, osteopathe", "Lyon, Paris, Lille")
    finally:
        app.discovery.search_places = saved_search
        app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN = saved_keys
    logs = out.getvalue()
    for expected in (
        "keywords saisis : 2", "zones saisies : 3", "requêtes générées : 6",
        "résultats Google bruts : 12", "résultats dédupliqués : 0",
        "prospects effectivement envoyés dans Notion : 0",
    ):
        check(expected in logs, f"log app présent : {expected}")


def test_zero_results_and_network_error_do_not_break_run():
    print("test_zero_results_and_network_error_do_not_break_run")
    original_search = app.discovery.search_places
    saved_keys = app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"
    try:
        def zero_results(keywords, cities, *, stats=None):
            return original_search(
                keywords, cities, stats=stats, sleep=lambda _seconds: None,
                fetch=lambda _params: {"status": "ZERO_RESULTS", "results": []},
            )

        app.discovery.search_places = zero_results
        zero_events = _drain("kine", "Lyon")

        calls = []

        def network_then_zero(params):
            calls.append(params)
            if len(calls) == 1:
                raise RuntimeError("réseau indisponible")
            return {"status": "ZERO_RESULTS", "results": []}

        def one_network_error(keywords, cities, *, stats=None):
            return original_search(
                keywords, cities, stats=stats, sleep=lambda _seconds: None,
                fetch=network_then_zero,
            )

        app.discovery.search_places = one_network_error
        error_events = _drain("kine, osteopathe", "Lyon")
    finally:
        app.discovery.search_places = original_search
        app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN = saved_keys

    check([e["type"] for e in zero_events] == ["searching", "places_found", "done"],
          "app : ZERO_RESULTS termine proprement le run")
    check([e["type"] for e in error_events] == ["searching", "places_found", "done"],
          "app : erreur réseau Google isolée, run non bloqué")
    check(len(calls) == 2, "app : la requête suivant l'erreur réseau est exécutée")


def test_place_details_error_does_not_break_run():
    print("test_place_details_error_does_not_break_run")
    saved_search = app.discovery.search_places
    saved_details = app.get_place_details
    saved_scrape = app.scrape_website
    saved_keys = app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"

    def fake_search(keyword, city, *, stats=None):
        stats.update({
            "keyword_count": 1, "zone_count": 1,
            "queries_generated": 1, "queries_executed": 1,
            "raw_results": 1, "deduped_results": 1,
        })
        return [{"place_id": "p1", "name": "Lead détails indisponibles"}]

    def boom(_place_id):
        raise RuntimeError("réseau détails indisponible")

    app.discovery.search_places = fake_search
    app.get_place_details = boom
    app.scrape_website = lambda _url: {
        "email": None, "facebook": None, "instagram": None, "text": "",
    }
    try:
        events = _drain("kine", "Lyon")
    finally:
        app.discovery.search_places = saved_search
        app.get_place_details = saved_details
        app.scrape_website = saved_scrape
        app.GOOGLE_API_KEY, app.ANTHROPIC_API_KEY, app.NOTION_TOKEN = saved_keys
    types = [e["type"] for e in events]
    check("error" not in types and types[-1] == "done",
          "app : erreur Google Place Details isolée, run terminé")
    check(next(e for e in events if e["type"] == "done")["eliminated"] == 1,
          "app : lead sans détails éliminé proprement en aval")


# --- Configuration incomplète : erreur claire (pas de silence) ---------------


def test_missing_key_surfaces_error():
    print("test_missing_key_surfaces_error")
    saved = app.GOOGLE_API_KEY
    app.GOOGLE_API_KEY = ""  # simule une clé manquante
    try:
        events = _drain("kine", "Lyon")
    finally:
        app.GOOGLE_API_KEY = saved
    check(len(events) == 1 and events[0]["type"] == "error",
          "clé manquante -> un unique événement 'error' (jamais un faux 0 résultat)")
    check("GOOGLE_API_KEY" in events[0]["message"], "le message nomme la clé absente")


# --- Gate d'export basé BPS + isolation des erreurs Claude (A1/A3) -----------


def _run_one_lead(analysis_fn, bps_value):
    """Exécute run_pipeline sur UN lead, tout réseau/scoring monkeypatché.

    analysis_fn : remplace analyze_with_claude (peut lever pour tester A3).
    bps_value   : BPS forcé via assign_bps -> pilote le gate should_export.
    Le lead fourni passe les filtres (téléphone + 50 avis + types neutres).
    Retourne (events, notion_calls, scrape_calls).
    """
    notion_calls = []
    scrape_calls = []
    saved = {k: getattr(app, k) for k in (
        "get_place_details", "scrape_website", "check_website_accessibility",
        "analyze_with_claude", "assign_friction", "assign_bps",
        "create_or_update_prospect",
        "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "NOTION_TOKEN",
    )}
    saved_search = app.discovery.search_places
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"

    def fake_search(keyword, city, *, stats=None):
        stats.update({
            "keyword_count": 1, "zone_count": 1,
            "queries_generated": 1, "queries_executed": 1,
            "raw_results": 1, "deduped_results": 1,
        })
        return [{
            "place_id": "p1", "name": "Lead Test",
            app.discovery.SOURCE_CONTEXT_KEY: [{"keyword": "coach sportif", "zone": "Lyon"}],
        }]

    app.discovery.search_places = fake_search
    app.get_place_details = lambda pid: {
        "name": "Lead Test", "formatted_phone_number": "01 23 45 67 89",
        "website": "", "types": ["point_of_interest", "establishment"],
        "user_ratings_total": 50, "rating": 4.5, "business_status": "OPERATIONAL",
    }
    app.scrape_website = lambda url: scrape_calls.append(url) or {"email": None, "facebook": None, "instagram": None, "text": ""}
    app.check_website_accessibility = lambda url: {"website_unreachable": None, "website_unreachable_reason": ""}
    app.analyze_with_claude = analysis_fn

    def fake_friction(p):
        p.friction_score = 0
        p.friction_flags = []

    def fake_bps(p):
        p.bps = bps_value

    def fake_notion(prospect, token=None, db_id=None):
        notion_calls.append(prospect)
        return ("created", "page1")

    app.assign_friction = fake_friction
    app.assign_bps = fake_bps
    app.create_or_update_prospect = fake_notion
    try:
        events = _drain("coach sportif", "Lyon")
    finally:
        app.discovery.search_places = saved_search
        for k, v in saved.items():
            setattr(app, k, v)
    return events, notion_calls, scrape_calls


def _ok_analysis(name, text, has_site):
    # Claude n'émet plus de score de pertinence : extracteur de faits seulement.
    # Le gate d'export est purement BPS (prouvé par les deux tests ci-dessous).
    return {
        "activites": "coach", "besoins": ["Pas de site"], "booking_software": "Aucun",
        "notes": "ok", "website_quality": "unknown",
        "booking_quality": "unknown", "cta_presence": "unknown", "website_freshness": "unknown",
    }


def test_gate_exports_when_bps_high():
    print("test_gate_exports_when_bps_high")
    events, notion_calls, scrape_calls = _run_one_lead(_ok_analysis, bps_value=75)
    types = [e["type"] for e in events]
    check("inserted" in types, "BPS 75 (>=75) -> événement inserted (gate = BPS pur, sans score Claude)")
    check("skipped" not in types, "BPS 75 -> aucun skipped")
    check(len(notion_calls) == 1, "BPS 75 -> Notion appelé exactement 1 fois")
    inserted = next(e for e in events if e["type"] == "inserted")
    check(inserted["bps"] == 75, "événement inserted porte bps=75")
    check(notion_calls[0].ville == "Lyon", "prospect exporté : ville alimentée (Lyon)")
    check(notion_calls[0].business_type == "coach_sportif", "prospect exporté : repli keyword -> coach_sportif")
    check(notion_calls[0].search_sources == [{"keyword": "coach sportif", "zone": "Lyon"}],
          "prospect exporté : contexte de recherche conservé en variable interne")
    check(scrape_calls == [""], "pipeline lancé : scrape_website est bien appelé")


def test_gate_skips_when_bps_low():
    print("test_gate_skips_when_bps_low")
    events, notion_calls, _scrape_calls = _run_one_lead(_ok_analysis, bps_value=40)
    types = [e["type"] for e in events]
    check("skipped" in types, "BPS 40 (<75) -> événement skipped")
    check("inserted" not in types, "BPS 40 -> aucun inserted")
    check(len(notion_calls) == 0, "BPS 40 -> Notion jamais appelé")
    skipped = next(e for e in events if e["type"] == "skipped")
    check(skipped.get("bps") == 40, "événement skipped porte bps=40 (front affiche le BPS, jamais de score Claude)")


def test_claude_failure_isolated():
    print("test_claude_failure_isolated")

    def boom(name, text, has_site):
        raise RuntimeError("Claude indisponible")

    events, notion_calls, _scrape_calls = _run_one_lead(boom, bps_value=75)
    types = [e["type"] for e in events]
    check("error" not in types, "échec Claude sur un lead -> run NON cassé (aucun event error)")
    check(types[-1] == "done", "le run se termine proprement par 'done'")
    check("analysis_done" in types, "analysis_done émis même en repli (bijection préservée)")
    analysis_done = next(e for e in events if e["type"] == "analysis_done")
    check(analysis_done.get("notes") == "Analyse Claude indisponible (appel en échec).",
          "repli neutre signalé dans notes")
    check(analysis_done.get("besoins") == [], "repli n'invente aucun besoin")
    check("inserted" in types and len(notion_calls) == 1,
          "lead conservé et exporté malgré l'échec Claude (BPS 75)")


# --- Cache local : Place ID récent -> skip sans réseau (B5) ------------------


def test_cache_skip_emits_event():
    print("test_cache_skip_emits_event")
    saved = {k: getattr(app, k) for k in (
        "get_place_details",
        "load_cache", "save_cache", "was_processed_recently", "mark_processed",
        "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "NOTION_TOKEN",
    )}
    saved_search = app.discovery.search_places
    app.GOOGLE_API_KEY = app.ANTHROPIC_API_KEY = app.NOTION_TOKEN = "x"

    def fake_search(keyword, city, *, stats=None):
        stats.update({
            "keyword_count": 1, "zone_count": 1,
            "queries_generated": 1, "queries_executed": 1,
            "raw_results": 1, "deduped_results": 1,
        })
        return [{"place_id": "p1", "name": "Déjà vu"}]

    app.discovery.search_places = fake_search
    detail_calls = []
    app.get_place_details = lambda pid: detail_calls.append(pid) or {}
    app.load_cache = lambda *a, **k: {}
    app.save_cache = lambda *a, **k: True
    app.was_processed_recently = lambda *a, **k: True  # tout est « récent »
    app.mark_processed = lambda *a, **k: None
    q = queue.Queue()
    try:
        app.run_pipeline("kine", "Lyon", q)
    finally:
        app.discovery.search_places = saved_search
        for k, v in saved.items():
            setattr(app, k, v)
    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)
    types = [e["type"] for e in events]
    check("cached_skip" in types, "Place ID récent -> événement cached_skip")
    check(detail_calls == [], "lead en cache -> aucun get_place_details (réseau/quota épargnés)")
    done = next(e for e in events if e["type"] == "done")
    check(done["cached"] == 1, "done : cached = 1")
    check(done["inserted"] == 0 and done["skipped"] == 0 and done["eliminated"] == 0,
          "done : aucun lead réellement traité")


def main():
    for test in (
        test_index_route_absolute_and_serves_app,
        test_health,
        test_stream_flow_no_network,
        test_app_csv_inputs_reach_discovery,
        test_event_generator_launches_pipeline,
        test_discovery_logs,
        test_zero_results_and_network_error_do_not_break_run,
        test_place_details_error_does_not_break_run,
        test_missing_key_surfaces_error,
        test_gate_exports_when_bps_high,
        test_gate_skips_when_bps_low,
        test_claude_failure_isolated,
        test_cache_skip_emits_event,
    ):
        test()

    print("\n" + "=" * 50)
    if _failures:
        print(f"ÉCHEC : {len(_failures)} test(s) en échec")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("OK : tous les tests passent")


if __name__ == "__main__":
    main()
