"""Tests du journal best-effort des breakdowns BPS (scorelog.py).

Runner autonome, SANS écriture disque : l'I/O (append/read) est injectée.
  python3 test_scorelog.py   (code 1 si au moins un test échoue)
"""

import json
import sys

from model import Prospect
from scorelog import (
    score_record,
    record_score,
    load_breakdowns,
    build_calibration_samples,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def _prospect():
    return Prospect(
        nom="X", place_id="PID-1", bps=82, business_type="beaute",
        bps_breakdown={
            "digital_need": {"score": 1.0, "weight": 20, "known": True, "contribution": 25.0},
            "financial_capacity": {"score": None, "weight": 20, "known": False, "contribution": 0.0},
        },
    )


def test_score_record_minimal():
    print("test_score_record_minimal")
    rec = score_record(_prospect())
    check(rec["place_id"] == "PID-1", "place_id conservé (clé de jointure)")
    check(rec["bps"] == 82 and rec["business_type"] == "beaute", "contexte bps + business_type conservé")
    check(set(rec["breakdown"].keys()) == {"digital_need", "financial_capacity"}, "toutes les dimensions journalisées")
    # On ne conserve que score + known (rien d'autre, aucun champ libre/secret).
    check(rec["breakdown"]["digital_need"] == {"score": 1.0, "known": True}, "dimension connue -> score + known")
    check(rec["breakdown"]["financial_capacity"] == {"score": None, "known": False}, "dimension inconnue -> score None préservé")


def test_record_score_appends_line():
    print("test_record_score_appends_line")
    lines = []
    ok = record_score(_prospect(), append=lines.append)
    check(ok is True, "record_score retourne True quand une ligne est écrite")
    check(len(lines) == 1, "une seule ligne journalisée")
    parsed = json.loads(lines[0])
    check(parsed["place_id"] == "PID-1", "ligne = JSON valide indexé par place_id")


def test_record_score_requires_place_id():
    print("test_record_score_requires_place_id")
    lines = []
    ok = record_score(Prospect(nom="sans id", place_id=""), append=lines.append)
    check(ok is False and lines == [], "sans place_id -> rien journalisé (pas de clé de jointure)")


def test_record_score_best_effort_swallows_errors():
    print("test_record_score_best_effort_swallows_errors")

    def boom(_line):
        raise IOError("disque plein")

    ok = record_score(_prospect(), append=boom)
    check(ok is False, "erreur d'I/O avalée -> False (le pipeline ne casse jamais)")


def test_load_breakdowns_last_wins_and_tolerant():
    print("test_load_breakdowns_last_wins_and_tolerant")
    content = "\n".join([
        json.dumps({"place_id": "A", "breakdown": {"digital_need": {"score": 0.5, "known": True}}}),
        "   ",                                              # ligne vide -> ignorée
        "{pas du json",                                     # ligne corrompue -> ignorée
        json.dumps({"breakdown": {}}),                      # sans place_id -> ignorée
        json.dumps({"place_id": "A", "breakdown": {"digital_need": {"score": 0.9, "known": True}}}),  # écrase A
    ])
    bd = load_breakdowns(read=lambda: content)
    check(set(bd.keys()) == {"A"}, "lignes vides/corrompues/sans clé ignorées (tolérant)")
    check(bd["A"]["digital_need"]["score"] == 0.9, "dernière ligne par place_id l'emporte")


def test_load_breakdowns_missing_is_empty():
    print("test_load_breakdowns_missing_is_empty")

    def boom():
        raise IOError("absent")

    check(load_breakdowns(read=boom) == {}, "lecture impossible -> {} (best effort, jamais d'exception)")


def test_build_calibration_samples_join():
    print("test_build_calibration_samples_join")
    breakdowns = {
        "A": {"digital_need": {"score": 1.0, "known": True}},
        "B": {"digital_need": {"score": 0.0, "known": True}},
        "Z": {"digital_need": {"score": 0.5, "known": True}},  # pas d'issue -> exclu
    }
    outcomes = {
        "A": {"outcome": "won"},
        "B": {"outcome": "lost"},
        "C": {"outcome": "won"},  # pas de breakdown -> exclu
    }
    samples = build_calibration_samples(breakdowns, outcomes)
    check(len(samples) == 2, "jointure = intersection des place_id (A, B)")
    check(sorted(s["outcome"] for s in samples) == ["lost", "won"], "issues rattachées aux bons breakdowns")
    check(all("breakdown" in s and "outcome" in s for s in samples), "format C2 = {outcome, breakdown}")
    check(build_calibration_samples({}, {}) == [], "sources vides -> []")


def main():
    for test in (
        test_score_record_minimal,
        test_record_score_appends_line,
        test_record_score_requires_place_id,
        test_record_score_best_effort_swallows_errors,
        test_load_breakdowns_last_wins_and_tolerant,
        test_load_breakdowns_missing_is_empty,
        test_build_calibration_samples_join,
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
