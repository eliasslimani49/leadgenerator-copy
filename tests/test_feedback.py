"""Tests de la boucle de feedback conversion (feedback.py).

Runner autonome, SANS réseau : l'I/O Notion est injectée (post -> json).
  python3 test_feedback.py   (code 1 si au moins un test échoue)
"""

import sys

from feedback import (
    NOTION_STATUT_OPTIONS,
    OUTCOME_LADDER,
    RESPONDED_OUTCOMES,
    STATUT_TO_OUTCOME,
    fetch_all_pages,
    load_outcomes,
    outcome_from_status,
    parse_outcomes,
    responded,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def _page(place_id=None, statut=None, bps=None):
    """Fabrique une page Notion minimale (formes réelles des propriétés)."""
    props = {}
    if place_id is not None:
        props["ID Client"] = {"rich_text": [{"plain_text": place_id}]}
    if statut is not None:
        props["Statut"] = {"select": {"name": statut}}
    if bps is not None:
        props["Opportunité commerciale"] = {"number": bps}
    return {"properties": props}


def test_outcome_from_status():
    print("test_outcome_from_status")
    check(outcome_from_status("Gagné") == "won", "Gagné -> won")
    check(outcome_from_status("Perdu") == "lost", "Perdu -> lost")
    check(outcome_from_status("RDV pris") == "meeting", "RDV pris -> meeting")
    check(outcome_from_status("Intéressé") == "replied", "Intéressé -> replied")
    check(outcome_from_status("Contacté") == "contacted", "Contacté -> contacted")
    check(outcome_from_status("  Gagné  ") == "won", "espaces tolérés")
    check(outcome_from_status("statut inconnu") == "none", "statut inconnu -> none (jamais supposé)")
    check(outcome_from_status("") == "none" and outcome_from_status(None) == "none", "vide/None -> none")


def test_statut_mapping_no_drift():
    """Garde-fou : chaque valeur RÉELLE du select Notion est explicitement mappée.

    Sans ça, un statut réel (ex. « Intéressé ») retomberait en "none" et serait
    invisible pour la calibration / le taux de réponse (sous-comptage silencieux).
    """
    print("test_statut_mapping_no_drift")
    for statut in NOTION_STATUT_OPTIONS:
        check(statut in STATUT_TO_OUTCOME,
              f"statut réel '{statut}' explicitement mappé (pas de chute silencieuse en none)")
        check(STATUT_TO_OUTCOME[statut] in OUTCOME_LADDER,
              f"'{statut}' -> issue valide de l'échelle")
    # Stades profonds non terminaux : comptés comme « a répondu », jamais won/lost.
    for statut in ("Intéressé", "RDV pris", "Proposition envoyée", "Bêta testeur"):
        check(responded(outcome_from_status(statut)),
              f"'{statut}' compte comme une réponse (engagement réel)")
        check(outcome_from_status(statut) not in ("won", "lost"),
              f"'{statut}' n'est ni gagné ni perdu (stade ouvert)")
    # Seuls Gagné/Perdu sont terminaux (signal de calibration C2).
    check(outcome_from_status("Gagné") == "won" and outcome_from_status("Perdu") == "lost",
          "seuls Gagné/Perdu sont terminaux (won/lost)")
    check(outcome_from_status("Prospect") == "none", "Prospect non contacté -> none")


def test_responded_helper():
    print("test_responded_helper")
    check(responded("won") and responded("replied") and responded("meeting"), "won/replied/meeting -> a répondu")
    check(not responded("contacted"), "contacté sans réponse -> non répondu")
    check(not responded("lost"), "perdu -> non compté comme réponse (pas d'inflation)")
    check(not responded("none"), "none -> non répondu")


def test_parse_outcomes():
    print("test_parse_outcomes")
    pages = [
        _page("p1", "Gagné", 92),
        _page("p2", "Prospect", 40),
        _page("p3", "Perdu", 75),
        _page(statut="Gagné", bps=99),           # sans Place ID -> ignorée
        {"properties": None},                      # malformée -> ignorée
        None,                                       # None -> ignorée
    ]
    out = parse_outcomes(pages)
    check(set(out.keys()) == {"p1", "p2", "p3"}, "seules les pages avec ID Client sont retenues")
    check(out["p1"] == {"statut": "Gagné", "outcome": "won", "bps": 92}, "p1 : issue + bps")
    check(out["p2"]["outcome"] == "none", "Prospect -> none")
    check(out["p3"]["outcome"] == "lost" and out["p3"]["bps"] == 75, "Perdu -> lost + bps")
    check(parse_outcomes([]) == {} and parse_outcomes(None) == {}, "vide/None -> {} (tolérant)")


def test_parse_reads_text_content_fallback():
    print("test_parse_reads_text_content_fallback")
    # ID Client fourni via text.content (et non plain_text) -> lu quand même.
    page = {"properties": {"ID Client": {"rich_text": [{"text": {"content": "pX"}}]},
                           "Statut": {"select": {"name": "Gagné"}}}}
    out = parse_outcomes([page])
    check("pX" in out and out["pX"]["outcome"] == "won", "ID Client via text.content + 'Gagné' -> won")


def test_fetch_pagination_injected():
    print("test_fetch_pagination_injected")
    calls = []

    def fake_post(body):
        calls.append(dict(body))
        if "start_cursor" not in body:
            return {"results": [_page("p1", "Gagné", 90)], "has_more": True, "next_cursor": "c2"}
        return {"results": [_page("p2", "Perdu", 72)], "has_more": False}

    pages = fetch_all_pages(token="x", db_id="db", post=fake_post)
    check(len(pages) == 2, "pagination : 2 pages agrégées")
    check(len(calls) == 2 and calls[1].get("start_cursor") == "c2", "2e appel utilise le curseur")
    outcomes = load_outcomes(token="x", db_id="db", post=fake_post)
    check(outcomes["p1"]["outcome"] == "won" and outcomes["p2"]["outcome"] == "lost",
          "load_outcomes = fetch + parse (lecture seule)")


def main():
    for test in (
        test_outcome_from_status,
        test_statut_mapping_no_drift,
        test_responded_helper,
        test_parse_outcomes,
        test_parse_reads_text_content_fallback,
        test_fetch_pagination_injected,
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
