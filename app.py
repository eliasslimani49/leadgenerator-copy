import os
import re
import json
import queue
import threading
import asyncio
import requests
from dotenv import load_dotenv
import anthropic
from model import (
    build_prospect,
    sanitize_choice,
    WEBSITE_QUALITIES,
    BOOKING_QUALITIES,
    CTA_PRESENCES,
    WEBSITE_FRESHNESS_VALUES,
)
from scraper import scrape_website, check_website_accessibility
import discovery
from filters import elimination_reasons
from friction import assign_friction
from bps import assign_bps
import ranking
import messaging
from notion_sync import create_or_update_prospect
from segmentation import should_export, qualification
from cache import load_cache, save_cache, was_processed_recently, mark_processed
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env", override=True)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_DB_ID = "3468c144ece78081ad5edd03993469a7"

VALID_BESOINS = [
    "Pas de réservation en ligne",
    "Pas de paiement en ligne",
    "Pas de site",
    "Réseaux sociaux inefficaces",
    "Perte de temps (gestion manuelle)",
    "Pas de clients",
    "Autre",
]

app = FastAPI()


def get_place_details(place_id):
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,website,user_ratings_total,rating,types,business_status",
        "key": GOOGLE_API_KEY,
        "language": "fr",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
    except Exception as exc:
        print(f"[Google Place Details erreur] place_id='{place_id}' "
              f"réseau={type(exc).__name__} — {exc}")
        return {}
    if data.get("status") not in (None, "OK"):
        print(f"[Google Place Details erreur] place_id='{place_id}' "
              f"status={data.get('status')} — {data.get('error_message', '')}")
        return {}
    return data.get("result", {})


def _source_context(place, fallback_keyword, fallback_zone):
    """Contexte interne de la première requête ayant remonté une place."""
    sources = place.get(discovery.SOURCE_CONTEXT_KEY) or []
    primary = sources[0] if sources else {}
    return (
        primary.get("keyword") or fallback_keyword,
        primary.get("zone") or fallback_zone,
        sources,
    )


def _log_discovery(stats):
    """Logs opérateur du balayage Google, sans modifier les événements métier."""
    print(f"[Découverte] keywords saisis : {stats.get('keyword_count', 0)}")
    print(f"[Découverte] zones saisies : {stats.get('zone_count', 0)}")
    print(f"[Découverte] requêtes générées : {stats.get('queries_generated', 0)} "
          f"(exécutées : {stats.get('queries_executed', 0)})")
    print(f"[Découverte] résultats Google bruts : {stats.get('raw_results', 0)}")
    print(f"[Découverte] résultats dédupliqués : {stats.get('deduped_results', 0)}")


def analyze_with_claude(name, site_text, has_website):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    context = site_text if site_text else f"Aucun site web trouvé pour '{name}'."
    prompt = f"""Tu es un EXTRACTEUR DE FAITS. À partir du contenu ci-dessous, tu relèves uniquement des éléments OBSERVABLES sur le professionnel nommé "{name}". Tu ne juges PAS sa pertinence commerciale (aucune note de pertinence) et tu n'inventes rien : si une information est absente, utilise la valeur prévue ("unknown" / "Aucun" / liste vide).

Contenu du site web :
\"\"\"
{context}
\"\"\"

Réponds UNIQUEMENT avec un objet JSON valide (sans markdown, sans explication) avec ces champs :
- "activites" : string décrivant les activités/métiers du professionnel
- "besoins" : array des MANQUES observés parmi ces valeurs exactes uniquement : "Pas de réservation en ligne", "Pas de paiement en ligne", "Pas de site", "Réseaux sociaux inefficaces", "Perte de temps (gestion manuelle)", "Pas de clients"
- "booking_software" : string COURTE = nom exact de l'outil de réservation/prise de RDV en ligne identifié (ex. "Planity", "Calendly", "Treatwell"), ou "Aucun" si aucun outil dédié n'est identifié. C'est un libellé technique, jamais une phrase.
- "booking_details" : string (1-2 phrases) décrivant FACTUELLEMENT comment la prise de rendez-vous / réservation fonctionne aujourd'hui : nomme l'outil dédié s'il y en a un, sinon précise le moyen observé (téléphone, lien sur un réseau social, formulaire de contact, e-mail…) ou indique qu'aucun outil permettant aux clients de réserver directement n'a été identifié. Factuel et observationnel, sans jugement commercial. Ex. : "Pas d'outil dédié à la réservation identifié : la prise de rendez-vous semble aujourd'hui passer uniquement par un lien disponible sur son compte Facebook."
- "notes" : string (2-4 phrases) — résumé FACTUEL et OBSERVATIONNEL du profil : ce que fait le professionnel, comment il gère concrètement ses rendez-vous / clients, et ce qui manque opérationnellement (ex. absence d'outil pour piloter les réservations). Décris les faits et leurs implications pratiques, jamais la pertinence commerciale ni une intention de vente. Ex. de ton : "La prise de rendez-vous se fait pour le moment via un lien disponible sur son compte Facebook. Elle ne dispose pas d'un outil permettant à ses clients de réserver directement ; je pense donc qu'elle n'a rien pour piloter ses réservations."
- "website_quality" : une valeur exacte parmi "none","poor","average","good" ("none" si aucun site)
- "booking_quality" : une valeur exacte parmi "none","basic","advanced" ("none" si aucune réservation en ligne)
- "cta_presence" : "present","absent" ou "unknown" — "present" si le site affiche un appel à l'action clair (réserver, prendre RDV, demander un devis, contact) ; "absent" s'il n'y en a aucun ; "unknown" si aucun site
- "website_freshness" : "fresh","outdated" ou "unknown" — "fresh" si design/contenu récent ; "outdated" si site vieillissant ; "unknown" si aucun site"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}
    data["besoins"] = [b for b in data.get("besoins", []) if b in VALID_BESOINS]
    data.setdefault("activites", "")
    data.setdefault("booking_software", "Aucun")
    data.setdefault("booking_details", "")
    data.setdefault("notes", "")
    data["website_quality"] = sanitize_choice(data.get("website_quality"), WEBSITE_QUALITIES, "unknown")
    data["booking_quality"] = sanitize_choice(data.get("booking_quality"), BOOKING_QUALITIES, "unknown")
    data["cta_presence"] = sanitize_choice(data.get("cta_presence"), CTA_PRESENCES, "unknown")
    data["website_freshness"] = sanitize_choice(data.get("website_freshness"), WEBSITE_FRESHNESS_VALUES, "unknown")
    return data


def _fallback_analysis():
    """Analyse neutre de repli quand l'appel Claude échoue sur un lead.

    N'invente aucun besoin ni irritant : tous les champs qualitatifs restent
    "unknown"/vides (donnée absente ≠ défaut). Le lead n'est pas perdu — la
    friction et le BPS continuent de tourner sur les signaux Google + scrape
    déjà collectés. Aucune note de pertinence : le gate d'export se base sur
    le BPS, jamais sur un jugement Claude.
    """
    return {
        "activites": "",
        "besoins": [],
        "booking_software": "Aucun",
        "booking_details": "",
        "notes": "Analyse Claude indisponible (appel en échec).",
        "website_quality": "unknown",
        "booking_quality": "unknown",
        "cta_presence": "unknown",
        "website_freshness": "unknown",
    }


def run_pipeline(keyword, city, q):
    try:
        missing = [n for n, v in (
            ("GOOGLE_API_KEY", GOOGLE_API_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("NOTION_TOKEN", NOTION_TOKEN),
        ) if not v]
        if missing:
            q.put({"type": "error", "message": f"Configuration incomplète : {', '.join(missing)} absente(s) du fichier .env."})
            return
        q.put({"type": "searching", "keyword": keyword, "city": city})
        discovery_stats = {}
        places = discovery.search_places(keyword, city, stats=discovery_stats)
        _log_discovery(discovery_stats)
        total = len(places)
        q.put({
            "type": "places_found",
            "count": total,
            "keyword_count": discovery_stats.get("keyword_count", 0),
            "zone_count": discovery_stats.get("zone_count", 0),
            "query_count": discovery_stats.get("queries_generated", 0),
            "raw_count": discovery_stats.get("raw_results", 0),
            "deduped_count": discovery_stats.get("deduped_results", total),
        })

        inserted = 0
        skipped = 0
        eliminated = 0
        cached = 0
        scored = []
        cache_data = load_cache()

        for i, place in enumerate(places):
            place_id = place.get("place_id", "")
            source_keyword, source_zone, search_sources = _source_context(place, keyword, city)
            print(f"[Découverte source] place_id='{place_id}' keyword='{source_keyword}' "
                  f"zone='{source_zone}'")
            if was_processed_recently(cache_data, place_id):
                cached += 1
                q.put({"type": "cached_skip", "name": place.get("name", "…"), "place_id": place_id})
                continue
            q.put({
                "type": "lead_start",
                "index": i + 1,
                "total": total,
                "name": place.get("name", "…"),
                "source_keyword": source_keyword,
                "source_zone": source_zone,
            })

            q.put({"type": "step", "step": "details"})
            try:
                details = get_place_details(place_id)
            except Exception as exc:
                print(f"[Google Place Details erreur] place_id='{place_id}' "
                      f"réseau={type(exc).__name__} — {exc}")
                details = {}
            nom = details.get("name") or place.get("name", "Inconnu")
            telephone = details.get("formatted_phone_number", "")
            site_web = details.get("website", "")
            q.put({"type": "details_done", "name": nom, "phone": telephone, "website": site_web})

            q.put({"type": "step", "step": "scrape"})
            scraped = scrape_website(site_web)
            q.put({"type": "scrape_done", "email": scraped["email"], "facebook": scraped["facebook"], "instagram": scraped["instagram"]})

            pre = build_prospect(
                nom=nom, telephone=telephone, site_web=site_web,
                scraped=scraped, analysis={}, google=details, place_id=place_id,
                ville=source_zone, keyword=source_keyword,
            )
            reasons = elimination_reasons(pre)
            if reasons:
                eliminated += 1
                mark_processed(cache_data, place_id, name=nom)
                q.put({"type": "eliminated", "name": nom, "reasons": reasons})
                continue

            q.put({"type": "step", "step": "claude"})
            try:
                analysis = analyze_with_claude(nom, scraped["text"], bool(site_web))
            except Exception as e:
                print(f"[Claude erreur] {nom} : {e} — repli analyse neutre, lead conservé.")
                analysis = _fallback_analysis()
            q.put({"type": "analysis_done", **analysis})

            # Accessibilité du site, sondée pour les SEULS survivants des filtres
            # éliminatoires — JAMAIS sur un lead déjà rejeté. Pourquoi ici :
            #   1) économie réseau : aucune requête gaspillée sur un lead écarté ;
            #   2) le flag ne sert qu'au modèle Messenger (image_pro/erreur), généré
            #      en aval pour les seuls leads qualifiés (gate BPS).
            # Enrichit `scraped` -> mappé sur le prospect dans build_prospect.
            scraped.update(check_website_accessibility(site_web))

            prospect = build_prospect(
                nom=nom, telephone=telephone, site_web=site_web,
                scraped=scraped, analysis=analysis, google=details, place_id=place_id,
                ville=source_zone, keyword=source_keyword,
            )
            prospect.search_sources = list(search_sources)
            assign_friction(prospect)
            q.put({"type": "friction_done", "name": nom, "score": prospect.friction_score, "flags": prospect.friction_flags})

            assign_bps(prospect)
            scored.append(prospect)
            q.put({"type": "bps_done", "name": nom, "bps": prospect.bps})

            if should_export(prospect.bps):
                q.put({"type": "step", "step": "notion"})
                try:
                    action, _page_id = create_or_update_prospect(
                        prospect, token=NOTION_TOKEN, db_id=NOTION_DB_ID
                    )
                    inserted += 1
                    mark_processed(cache_data, place_id, name=nom, bps=prospect.bps)
                    q.put({"type": "inserted", "name": nom, "action": action, "friction_score": prospect.friction_score, "bps": prospect.bps, "besoins": analysis["besoins"], "phone": telephone, "website": site_web, "email": scraped["email"]})
                except Exception as e:
                    q.put({"type": "notion_error", "name": nom, "message": str(e)})
            else:
                skipped += 1
                mark_processed(cache_data, place_id, name=nom, bps=prospect.bps)
                q.put({"type": "skipped", "name": nom, "bps": prospect.bps, "besoins": analysis["besoins"], "notes": analysis["notes"]})

        save_cache(cache_data)
        top = [
            {
                "name": p.nom,
                "bps": p.bps,
                "qualification": qualification(p.bps),
                "angle": messaging.message_angle(p.bps)[1],
                "ville": p.ville,
                "business_type": p.business_type,
                "friction_score": p.friction_score,
            }
            for p in ranking.select_elite(scored, limit=ranking.TOP_STRICT)
        ]
        stats = ranking.compute_stats(scored)
        print(f"[Pipeline] prospects effectivement envoyés dans Notion : {inserted}")
        q.put({"type": "done", "total": total, "inserted": inserted, "notion_sent": inserted, "skipped": skipped, "eliminated": eliminated, "cached": cached, "top": top, "stats": stats})
    except Exception as e:
        q.put({"type": "error", "message": str(e)})
    finally:
        q.put(None)


async def event_generator(keyword, city):
    q = queue.Queue()
    thread = threading.Thread(target=run_pipeline, args=(keyword, city, q), daemon=True)
    thread.start()
    while True:
        try:
            event = q.get(timeout=0.5)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except queue.Empty:
            yield ": keepalive\n\n"
            await asyncio.sleep(0.1)


@app.get("/stream")
async def stream(keyword: str, city: str):
    return StreamingResponse(
        event_generator(keyword, city),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


INDEX_HTML = Path(__file__).parent / "templates" / "index.html"


@app.get("/health")
async def health():
    """Sonde de disponibilité : confirme que le serveur tourne et signale les clés manquantes."""
    missing = [name for name, val in (
        ("GOOGLE_API_KEY", GOOGLE_API_KEY),
        ("NOTION_TOKEN", NOTION_TOKEN),
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
    ) if not val]
    return {"status": "ok", "missing_keys": missing}


@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    missing = [n for n, v in (
        ("GOOGLE_API_KEY", GOOGLE_API_KEY),
        ("NOTION_TOKEN", NOTION_TOKEN),
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
    ) if not v]
    if missing:
        print(f"[!] Clés absentes de .env : {', '.join(missing)} — le pipeline échouera tant qu'elles ne sont pas renseignées.")
    print(f"[Vitryne] Interface prête : http://{host}:{port}   (Ctrl+C pour arrêter)")
    uvicorn.run(app, host=host, port=port)
