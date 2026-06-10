import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Test end-to-end déterministe du pipeline Vitryne (SANS réseau).

Chaîne réelle exécutée sur des leads FABRIQUÉS (aucun appel Google/Claude/Notion,
aucun fichier touché) :

  build_prospect -> elimination_reasons -> assign_friction -> assign_bps
  -> qualification / should_export            (gate d'export = BPS pur)
  -> Notion (I/O monkeypatchée, store mémoire) (déduplication par Place ID)
  -> feedback.parse_outcomes                  (round-trip écrit -> lu)
  -> messaging.response_rates / calibration.propose_weights

Objectifs prouvés de bout en bout :
  - un lead complet passe les filtres et obtient un BPS borné ;
  - moins de 20 avis -> éliminé AVANT scoring ;
  - synchroniser deux fois le même Place ID ne crée PAS de doublon ;
  - les propriétés ÉCRITES par notion_sync sont RELUES sans perte par feedback
    (Place ID en text.content + statut réel -> issue canonique) ;
  - response_rates et propose_weights tournent sur ces issues sans rien inventer.

  python3 test_e2e.py   (code 1 si au moins un test échoue)
"""

import contextlib
import io
import sys

from targetly.core import calibration
from targetly.platform.integrations import feedback
from targetly.core import messaging
from targetly.platform.integrations import notion_sync
from targetly.cli import leads as vl
from targetly.core.bps import BPS_WEIGHTS, assign_bps
from targetly.core.filters import elimination_reasons
from targetly.core.friction import assign_friction
from targetly.core.model import build_prospect
from targetly.core.segmentation import QUALIFICATION_VALUES, qualification, should_export

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


_SCRAPED = {"email": None, "facebook": None, "instagram": None, "text": "", "booking_software": None}
_ANALYSIS = {
    "activites": "coach sportif", "besoins": ["Pas de site"], "booking_software": "Aucun",
    "notes": "profil factuel", "website_quality": "unknown", "booking_quality": "unknown",
    "cta_presence": "unknown", "website_freshness": "unknown",
}


def _make(place_id="pE2E", n_reviews=50, keyword="coach sportif"):
    """Lead réaliste qui passe les filtres (tél + avis suffisants + types neutres)."""
    google = {
        "name": "Lead E2E", "formatted_phone_number": "01 23 45 67 89", "website": "",
        "types": ["point_of_interest", "establishment"],
        "user_ratings_total": n_reviews, "rating": 4.7,
        "business_status": "OPERATIONAL",
    }
    return build_prospect(
        nom="Lead E2E", telephone="01 23 45 67 89", site_web="",
        scraped=dict(_SCRAPED), analysis=dict(_ANALYSIS),
        google=google, place_id=place_id, ville="Lyon", keyword=keyword,
    )


# --- Chaîne déterministe : éligibilité -> friction -> BPS -> gate ------------


def test_chain_eligible_to_gate():
    print("test_chain_eligible_to_gate")
    p = _make(n_reviews=50)
    check(elimination_reasons(p) == [], "lead complet -> non éliminé (éligible au scoring)")
    assign_friction(p)
    assign_bps(p)
    check(isinstance(p.bps, int) and 0 <= p.bps <= 100, "BPS entier borné [0,100]")
    q = qualification(p.bps)
    check(q in QUALIFICATION_VALUES, "qualification ∈ contrat 4 bandes")
    # Le gate d'export est BPS pur et cohérent avec la bande (jamais 'Ignorer' exporté).
    check(should_export(p.bps) == (q != "Ignorer"), "should_export <-> qualification (gate = BPS pur)")
    # Donnée absente ≠ 0 : une dimension inconnue reste à None dans le breakdown.
    for dim, entry in p.bps_breakdown.items():
        if not entry["known"]:
            check(entry["score"] is None, f"dimension inconnue '{dim}' -> score None (jamais 0)")


def test_chain_eliminates_low_reviews():
    print("test_chain_eliminates_low_reviews")
    p = _make(place_id="pLow", n_reviews=10)  # < MIN_REVIEWS (20)
    reasons = elimination_reasons(p)
    check("reviews_below_min" in reasons, "moins de 20 avis -> éliminé AVANT scoring")


# --- Déduplication Notion par Place ID (I/O en mémoire) ----------------------


def test_notion_dedup_no_duplicate():
    print("test_notion_dedup_no_duplicate")
    store = {}
    counter = {"n": 0}
    saved = {k: getattr(notion_sync, k) for k in
             ("find_page_id_by_place_id", "create_page", "update_page")}

    def fake_find(place_id, *, token, db_id):
        rec = store.get(place_id)
        return rec["page_id"] if rec else None

    def fake_create(prospect, *, token, db_id):
        counter["n"] += 1
        pid = f"page{counter['n']}"
        store[prospect.place_id] = {"page_id": pid, "props": notion_sync.create_properties(prospect)}
        return pid

    def fake_update(page_id, prospect, *, token):
        for rec in store.values():
            if rec["page_id"] == page_id:
                rec["props"].update(notion_sync.engine_properties(prospect))  # champs moteur seuls
        return page_id

    notion_sync.find_page_id_by_place_id = fake_find
    notion_sync.create_page = fake_create
    notion_sync.update_page = fake_update
    try:
        p = _make(place_id="pDedup")
        assign_friction(p)
        assign_bps(p)
        action1, id1 = notion_sync.create_or_update_prospect(p, token="x", db_id="db")
        action2, id2 = notion_sync.create_or_update_prospect(p, token="x", db_id="db")
    finally:
        for k, v in saved.items():
            setattr(notion_sync, k, v)

    check(action1 == "created", "1re sync d'un Place ID inédit -> created")
    check(action2 == "updated", "2e sync du même Place ID -> updated (pas de re-création)")
    check(id1 == id2, "même page_id réutilisé -> aucun doublon CRM")
    check(len(store) == 1, "store CRM : exactement 1 fiche pour 2 synchronisations")


# --- Round-trip writer <-> reader : ce qu'on écrit est relu sans perte -------


def test_writer_reader_round_trip():
    print("test_writer_reader_round_trip")
    p = _make(place_id="pRT")
    assign_friction(p)
    assign_bps(p)
    props = notion_sync.create_properties(p)  # forme RÉELLE écrite dans Notion
    check("ID Client" in props, "create_properties écrit l'ID Client (clé de jointure)")

    # La fiche est créée en 'Prospect' ; on simule la saisie humaine du statut et
    # on vérifie que feedback relit la fiche écrite (text.content) sans perte.
    for statut, expected in (("RDV pris", "meeting"), ("Gagné", "won"),
                             ("Perdu", "lost"), ("Intéressé", "replied")):
        page_props = dict(props)
        page_props["Statut"] = {"select": {"name": statut}}
        out = feedback.parse_outcomes([{"properties": page_props}])
        rec = out.get("pRT", {})
        check(rec.get("outcome") == expected, f"écrit puis relu : '{statut}' -> {expected}")
        check(rec.get("bps") == p.bps, f"BPS relu depuis la fiche écrite ('{statut}')")


# --- Issues réelles -> messaging + calibration (sans rien inventer) ----------


def test_feedback_to_messaging_and_calibration():
    print("test_feedback_to_messaging_and_calibration")
    outcomes = {
        "a": {"outcome": "won", "bps": 92},        # Exceptionnel, a répondu
        "b": {"outcome": "contacted", "bps": 85},  # Prioritaire, pas de réponse
        "c": {"outcome": "meeting", "bps": 84},    # Prioritaire, a répondu
        "d": {"outcome": "none", "bps": 75},       # Opportunité, jamais contacté
    }
    rates = messaging.response_rates(outcomes)
    check(rates["Exceptionnel"]["rate"] == 1.0, "Exceptionnel : 1/1 répondu")
    check(rates["Prioritaire"]["rate"] == 0.5, "Prioritaire : 1/2 répondu")
    check(rates["Opportunité"]["rate"] is None,
          "Opportunité jamais contacté -> rate None (donnée absente ≠ 0)")

    # Données insuffisantes -> proposition INCHANGÉE et non fiable (pas d'optim. prématurée).
    samples = [{"outcome": "won", "breakdown": {}}, {"outcome": "lost", "breakdown": {}}]
    result = calibration.propose_weights(samples)
    check(result["applied"] is False, "calibration jamais auto-appliquée")
    check(result["sufficient_data"] is False, "2 issues -> données insuffisantes")
    check(result["proposed_weights"] == dict(BPS_WEIGHTS), "poids BPS inchangés")
    check(sum(result["proposed_weights"].values()) == sum(BPS_WEIGHTS.values()),
          "somme des poids préservée (100)")


# --- CLI dry-run : zéro effet de bord (sécurise lecture/écriture) ------------


def _run_cli(dry_run):
    """Exécute vitryne_leads.main() avec toute l'I/O monkeypatchée (réseau + disque).

    Compte les effets de bord (Notion / cache / journal). Le gate should_export
    est forcé à True pour atteindre déterministiquement la branche d'écriture.
    """
    calls = {"notion": 0, "save_cache": 0, "record_run": 0, "scorelog": 0}
    keys = (
        "search_places", "get_place_details", "scrape_website", "check_website_accessibility",
        "analyze_with_claude", "assign_bps", "should_export", "create_or_update_prospect",
        "load_cache", "save_cache", "mark_processed",
        "GOOGLE_API_KEY", "NOTION_TOKEN", "ANTHROPIC_API_KEY",
    )
    saved = {k: getattr(vl, k) for k in keys}
    saved_record = vl.monitoring.record_run
    saved_score = vl.scorelog.record_score
    saved_argv = sys.argv

    vl.GOOGLE_API_KEY = vl.NOTION_TOKEN = vl.ANTHROPIC_API_KEY = "x"
    vl.search_places = lambda k, c: [{"place_id": "p1", "name": "Lead CLI"}]
    vl.get_place_details = lambda pid: {
        "name": "Lead CLI", "formatted_phone_number": "01 23 45 67 89", "website": "",
        "types": ["point_of_interest", "establishment"],
        "user_ratings_total": 50, "rating": 4.6, "business_status": "OPERATIONAL",
    }
    vl.scrape_website = lambda url: dict(_SCRAPED)
    vl.check_website_accessibility = lambda url: {"website_unreachable": None, "website_unreachable_reason": ""}
    vl.analyze_with_claude = lambda *a, **k: dict(_ANALYSIS)

    def fake_bps(p):
        p.bps = 80
        p.bps_breakdown = {}
        return p

    vl.assign_bps = fake_bps
    vl.should_export = lambda b: True
    vl.create_or_update_prospect = lambda *a, **k: (calls.__setitem__("notion", calls["notion"] + 1), ("created", "pX"))[1]
    vl.load_cache = lambda: {}
    vl.save_cache = lambda d: calls.__setitem__("save_cache", calls["save_cache"] + 1)
    vl.mark_processed = lambda *a, **k: None
    vl.monitoring.record_run = lambda *a, **k: calls.__setitem__("record_run", calls["record_run"] + 1)
    # Journal des scores : compté, jamais écrit sur disque (e2e ne touche aucun fichier).
    vl.scorelog.record_score = lambda *a, **k: calls.__setitem__("scorelog", calls["scorelog"] + 1)

    sys.argv = ["vitryne_leads.py", "kine", "Lyon"] + (["--dry-run"] if dry_run else [])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            vl.main()
    finally:
        for k, v in saved.items():
            setattr(vl, k, v)
        vl.monitoring.record_run = saved_record
        vl.scorelog.record_score = saved_score
        sys.argv = saved_argv
    return calls


def test_cli_dry_run_is_side_effect_free():
    print("test_cli_dry_run_is_side_effect_free")
    dry = _run_cli(dry_run=True)
    check(dry["notion"] == 0, "dry-run : create_or_update_prospect JAMAIS appelé (zéro écriture Notion)")
    check(dry["save_cache"] == 0, "dry-run : save_cache JAMAIS appelé (zéro écriture disque)")
    check(dry["record_run"] == 0, "dry-run : run non journalisé (zéro effet de bord)")
    check(dry["scorelog"] == 0, "dry-run : journal des scores JAMAIS écrit (zéro effet de bord)")

    live = _run_cli(dry_run=False)
    check(live["notion"] == 1, "mode réel : Notion appelé (la branche d'écriture est bien atteinte)")
    check(live["save_cache"] == 1, "mode réel : cache sauvegardé")
    check(live["record_run"] == 1, "mode réel : run journalisé (monitoring actif)")
    check(live["scorelog"] == 1, "mode réel : breakdown journalisé (base de calibration C2)")


def main():
    for test in (
        test_chain_eligible_to_gate,
        test_chain_eliminates_low_reviews,
        test_notion_dedup_no_duplicate,
        test_writer_reader_round_trip,
        test_feedback_to_messaging_and_calibration,
        test_cli_dry_run_is_side_effect_free,
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
