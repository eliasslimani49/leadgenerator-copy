"""Tests de la calibration déterministe des poids BPS (calibration.py).

Runner autonome, sans dépendance externe :  python3 test_calibration.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from bps import BPS_WEIGHTS
from calibration import (
    MIN_PER_DIM,
    MIN_WON_TOTAL,
    format_report,
    propose_weights,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def _sample(outcome, **dim_scores):
    """Échantillon {outcome, breakdown} : dim=score -> dimension connue."""
    breakdown = {dim: {"known": True, "score": score} for dim, score in dim_scores.items()}
    return {"outcome": outcome, "breakdown": breakdown}


def test_insufficient_data_keeps_weights():
    print("test_insufficient_data_keeps_weights")
    samples = [_sample("won", digital_need=1.0) for _ in range(3)] + \
              [_sample("lost", digital_need=0.0) for _ in range(3)]
    res = propose_weights(samples)
    check(res["sufficient_data"] is False, "trop peu de gagnés/perdus -> non fiable")
    check(res["applied"] is False, "jamais appliqué automatiquement")
    check(res["proposed_weights"] == dict(BPS_WEIGHTS), "données insuffisantes -> poids INCHANGÉS")


def test_discriminative_dimension_gains_weight():
    print("test_discriminative_dimension_gains_weight")
    # digital_need discrimine fortement (1.0 chez les gagnés, 0.0 chez les perdus) ;
    # financial_capacity ne discrimine pas (0.5 partout) -> doit perdre du poids.
    n = max(MIN_WON_TOTAL, MIN_PER_DIM) + 2
    won = [_sample("won", digital_need=1.0, financial_capacity=0.5) for _ in range(n)]
    lost = [_sample("lost", digital_need=0.0, financial_capacity=0.5) for _ in range(n)]
    res = propose_weights(won + lost)
    pw = res["proposed_weights"]
    check(res["sufficient_data"] is True, "assez de données -> proposition fiable")
    check(pw["digital_need"] > BPS_WEIGHTS["digital_need"], "dimension discriminante -> poids AUGMENTÉ")
    check(pw["financial_capacity"] < BPS_WEIGHTS["financial_capacity"], "dimension non discriminante -> poids RÉDUIT")
    check(sum(pw.values()) == 100, f"somme des poids = 100 (obtenu {sum(pw.values())})")
    check(all(v > 0 for v in pw.values()), "tous les poids restent > 0 (aucune dimension annulée)")


def test_dimension_without_data_is_kept():
    print("test_dimension_without_data_is_kept")
    n = max(MIN_WON_TOTAL, MIN_PER_DIM) + 2
    won = [_sample("won", digital_need=1.0, financial_capacity=0.5) for _ in range(n)]
    lost = [_sample("lost", digital_need=0.0, financial_capacity=0.5) for _ in range(n)]
    res = propose_weights(won + lost)
    pw = res["proposed_weights"]
    # current_friction n'est jamais renseigné -> non calibrable -> poids conservé.
    check(pw["current_friction"] == BPS_WEIGHTS["current_friction"], "dimension sans donnée -> poids conservé")
    row = next(r for r in res["report"] if r["dimension"] == "current_friction")
    check(row["status"].startswith("kept"), "rapport : dimension sans donnée marquée 'kept'")


def test_never_mutates_global_weights():
    print("test_never_mutates_global_weights")
    snapshot = dict(BPS_WEIGHTS)
    n = max(MIN_WON_TOTAL, MIN_PER_DIM) + 2
    propose_weights([_sample("won", digital_need=1.0, financial_capacity=0.5) for _ in range(n)] +
                    [_sample("lost", digital_need=0.0, financial_capacity=0.5) for _ in range(n)])
    check(BPS_WEIGHTS == snapshot, "BPS_WEIGHTS JAMAIS muté (proposition seulement)")


def test_empty_and_report():
    print("test_empty_and_report")
    res = propose_weights([])
    check(res["proposed_weights"] == dict(BPS_WEIGHTS) and res["sufficient_data"] is False,
          "aucun échantillon -> inchangé + non fiable")
    txt = format_report(res)
    check(isinstance(txt, str) and "Calibration BPS" in txt, "rapport humain lisible généré")
    check("NON FIABLE" in txt, "rapport signale explicitement le manque de données")


def main():
    for test in (
        test_insufficient_data_keeps_weights,
        test_discriminative_dimension_gains_weight,
        test_dimension_without_data_is_kept,
        test_never_mutates_global_weights,
        test_empty_and_report,
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
