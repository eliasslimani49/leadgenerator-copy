import sys
import os
import re
import json
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
from discovery import search_places
from filters import elimination_reasons
from friction import assign_friction
from bps import assign_bps
import ranking
import messaging
import monitoring
import scorelog
import feedback
import calibration
from notion_sync import create_or_update_prospect
from segmentation import should_export, qualification, EXPORT_MIN_BPS, QUALIFICATION_VALUES
from cache import load_cache, save_cache, was_processed_recently, mark_processed
import csv_export

load_dotenv()

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


def get_place_details(place_id: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,website,user_ratings_total,rating,types,business_status",
        "key": GOOGLE_API_KEY,
        "language": "fr",
    }
    resp = requests.get(url, params=params, timeout=10)
    return resp.json().get("result", {})


def analyze_with_claude(name: str, site_text: str, has_website: bool) -> dict:
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

    # Sanitize besoins
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

    N'invente aucun besoin ni irritant (tout reste "unknown") : le lead n'est
    pas perdu, friction et BPS tournent sur les signaux Google + scrape déjà
    collectés. Aucune note de pertinence — le gate d'export se base sur le BPS,
    jamais sur un jugement Claude.
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


def _parse_args(argv):
    """Sépare flags (--dry-run / --healthcheck / --report / --csv[=chemin]) et
    positionnels (mot-clé, ville). csv_path = None si --csv absent."""
    flags = {a for a in argv if a.startswith("--")}
    positional = [a for a in argv if not a.startswith("--")]
    csv_path = None
    for flag in flags:
        if flag == "--csv":
            csv_path = csv_export.DEFAULT_CSV_PATH
        elif flag.startswith("--csv="):
            csv_path = flag.split("=", 1)[1] or csv_export.DEFAULT_CSV_PATH
    return positional, ("--dry-run" in flags), ("--healthcheck" in flags), ("--report" in flags), csv_path


def _run_report():
    """Rapport LECTURE SEULE (aucune écriture, aucun poids muté) :
      - taux de réponse par bande de qualification (C3), depuis les issues Notion ;
      - proposition de calibration des poids BPS (C2), en joignant le journal local
        des breakdowns (scorelog) aux issues Notion par ID Client.

    La calibration n'est JAMAIS appliquée : c'est une proposition à valider à la
    main (qualité > automatisation). Tant que peu d'issues gagné/perdu existent,
    elle reste explicitement « non fiable » et les poids restent inchangés.
    """
    outcomes = feedback.load_outcomes(token=NOTION_TOKEN, db_id=NOTION_DB_ID)
    rates = messaging.response_rates(outcomes)
    print("Taux de réponse par bande (issues Notion réelles) :")
    for band in QUALIFICATION_VALUES:
        slot = rates.get(band, {})
        rate = slot.get("rate")
        rate_s = f"{rate:.0%}" if rate is not None else "—"
        print(f"  {band:<13} contactés {slot.get('contacted', 0):>3} | réponses {slot.get('responded', 0):>3} | taux {rate_s}")

    samples = scorelog.build_calibration_samples(scorelog.load_breakdowns(), outcomes)
    print(f"\n{len(samples)} prospect(s) avec breakdown journalisé ET issue connue (base de calibration).")
    print(calibration.format_report(calibration.propose_weights(samples)))


def main():
    positional, dry_run, do_healthcheck, do_report, csv_path = _parse_args(sys.argv[1:])

    if do_healthcheck:
        # Diagnostic lecture seule : présence des clés + base Notion joignable.
        result = monitoring.healthcheck(token=NOTION_TOKEN, db_id=NOTION_DB_ID)
        print(monitoring.format_health(result))
        sys.exit(0 if result["healthy"] else 1)

    if do_report:
        # Bilan lecture seule (taux de réponse C3 + proposition calibration C2).
        _run_report()
        sys.exit(0)

    if len(positional) < 2:
        print("Usage: python vitryne_leads.py \"mot-clé[,mot-clé2,...]\" \"zone[,zone2,...]\" [--dry-run] [--csv[=chemin]] [--healthcheck] [--report]")
        print("       Balayage élargi : séparez les mots-clés et les zones (quartiers/villes) par des virgules.")
        sys.exit(1)

    keyword, city = positional[0], positional[1]

    if dry_run:
        print("\n[dry-run] MODE SIMULATION : aucune écriture Notion ni cache.")
    print(f"\n🔍 Recherche : '{keyword}' à {city}\n")

    places = search_places(keyword, city)
    print(f"{len(places)} résultats Google Places trouvés.\n")

    total = 0
    inserted = 0
    rejected = 0
    cached = 0
    errors = 0
    scored = []
    cache_data = load_cache()

    for place in places:
        place_id = place.get("place_id", "")
        if was_processed_recently(cache_data, place_id):
            cached += 1
            print(f"[cache] {place.get('name', '…')} — déjà traité récemment, ignoré.")
            continue
        total += 1
        details = get_place_details(place_id)

        nom = details.get("name") or place.get("name", "Inconnu")
        telephone = details.get("formatted_phone_number", "")
        site_web = details.get("website", "")

        print(f"[{total}/{len(places)}] {nom}")

        scraped = scrape_website(site_web)

        pre = build_prospect(
            nom=nom, telephone=telephone, site_web=site_web,
            scraped=scraped, analysis={}, google=details, place_id=place_id,
            ville=city, keyword=keyword,
        )
        reasons = elimination_reasons(pre)
        if reasons:
            rejected += 1
            mark_processed(cache_data, place_id, name=nom)
            print(f"    ✗ Rejeté avant scoring : {', '.join(reasons)}")
            continue

        try:
            analysis = analyze_with_claude(nom, scraped["text"], bool(site_web))
        except Exception as e:
            print(f"    ! Claude indisponible : {e} — repli analyse neutre, lead conservé.")
            analysis = _fallback_analysis()

        notes = analysis.get("notes", "")
        if notes:
            print(f"    Faits : {notes}")

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
            ville=city, keyword=keyword,
        )
        assign_friction(prospect)
        print(f"    Friction : {prospect.friction_score} — {', '.join(prospect.friction_flags) or 'aucun irritant'}")

        assign_bps(prospect)
        scored.append(prospect)
        print(f"    BPS : {prospect.bps}/100")

        if should_export(prospect.bps):
            if dry_run:
                inserted += 1
                print(f"    [dry-run] Notion ignoré — aurait créé/màj la fiche (BPS {prospect.bps})")
            else:
                # Journal best effort du breakdown (jointure C2 ultérieure par
                # place_id avec les issues Notion) : n'échoue jamais le pipeline.
                scorelog.record_score(prospect)
                try:
                    action, _page_id = create_or_update_prospect(
                        prospect, token=NOTION_TOKEN, db_id=NOTION_DB_ID
                    )
                    inserted += 1
                    mark_processed(cache_data, place_id, name=nom, bps=prospect.bps)
                    print(f"    ✓ Notion : {action}")
                except Exception as e:
                    errors += 1
                    print(f"    ✗ Échec Notion : {e}")
        else:
            mark_processed(cache_data, place_id, name=nom, bps=prospect.bps)
            print(f"    → Ignoré (BPS {prospect.bps} < {EXPORT_MIN_BPS})")

    if dry_run:
        print("\n[dry-run] cache NON sauvegardé, aucune écriture effectuée.")
    else:
        save_cache(cache_data)

    # Export CSV optionnel (--csv), sortie additionnelle en fin de run : même
    # gate should_export que Notion, respecte le dry-run global. Un échec
    # d'écriture est VISIBLE et compté, mais n'annule pas le reste du run.
    if csv_path:
        try:
            csv_result = csv_export.export_csv(scored, csv_path, dry_run=dry_run)
            print(csv_export.format_export_report(csv_result))
        except OSError as e:
            errors += 1
            print(f"✗ Échec export CSV ({csv_path}) : {e}")

    stats = ranking.compute_stats(scored)
    top = ranking.select_elite(scored, limit=ranking.TOP_STRICT)

    counters = {
        "keyword": keyword, "city": city, "dry_run": dry_run,
        "total": total, "inserted": inserted, "rejected": rejected,
        "cached": cached, "errors": errors,
    }
    # Journal best effort (jamais en dry-run : aucun effet de bord en simulation).
    if not dry_run:
        monitoring.record_run(counters)

    print(f"\n{'='*50}")
    print(monitoring.run_report(counters))
    print(f"Résumé : {total} analysés, {rejected} rejetés (filtres), {inserted} {'simulés' if dry_run else 'insérés'} (Notion), {cached} ignorés (cache), {errors} erreurs.")
    if stats["total"]:
        print(f"BPS — min {stats['bps_min']} / médiane {stats['bps_median']} / moyenne {stats['bps_avg']} / max {stats['bps_max']}  |  élite (BPS≥{stats['min_bps']}) : {stats['elite_count']}")
    if top:
        print(f"\nTop {len(top)} prospects :")
        for rang, p in enumerate(top, 1):
            ville = f" — {p.ville}" if p.ville else ""
            angle = messaging.message_angle(p.bps)[1]
            print(f"  {rang}. {p.nom} — BPS {p.bps} ({qualification(p.bps)}, angle: {angle}){ville}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
