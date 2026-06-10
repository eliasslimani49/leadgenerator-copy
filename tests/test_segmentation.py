import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests de la segmentation commerciale (segmentation.py).

Runner autonome, sans dépendance externe :  python3 test_segmentation.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.core.segmentation import (
    EXPORT_MIN_BPS,
    QUALIFICATION_DEFAULT,
    QUALIFICATION_VALUES,
    qualification,
    should_export,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


# --- Bandes de qualification -------------------------------------------------


def test_bands_exceptionnel():
    print("test_bands_exceptionnel")
    check(qualification(100) == "Exceptionnel", "100 -> Exceptionnel")
    check(qualification(95) == "Exceptionnel", "95 -> Exceptionnel")
    check(qualification(90) == "Exceptionnel", "90 (borne basse) -> Exceptionnel")


def test_bands_prioritaire():
    print("test_bands_prioritaire")
    check(qualification(89) == "Prioritaire", "89 (juste sous 90) -> Prioritaire")
    check(qualification(85) == "Prioritaire", "85 -> Prioritaire")
    check(qualification(80) == "Prioritaire", "80 (borne basse) -> Prioritaire")


def test_bands_opportunite():
    print("test_bands_opportunite")
    check(qualification(79) == "Opportunité", "79 (juste sous 80) -> Opportunité")
    check(qualification(76) == "Opportunité", "76 -> Opportunité")
    check(qualification(75) == "Opportunité", "75 (borne basse relevée) -> Opportunité")


def test_bands_ignorer():
    print("test_bands_ignorer")
    check(qualification(74) == "Ignorer", "74 (juste sous 75) -> Ignorer")
    check(qualification(70) == "Ignorer", "70 -> Ignorer (seuil relevé : n'est plus exporté)")
    check(qualification(30) == "Ignorer", "30 -> Ignorer")
    check(qualification(0) == "Ignorer", "0 -> Ignorer")


def test_non_numeric_defaults_to_ignorer():
    print("test_non_numeric_defaults_to_ignorer")
    check(qualification(None) == QUALIFICATION_DEFAULT, "None -> Ignorer (jamais promu)")
    check(qualification("") == QUALIFICATION_DEFAULT, "'' -> Ignorer")
    check(qualification("abc") == QUALIFICATION_DEFAULT, "texte -> Ignorer")


def test_values_contract():
    print("test_values_contract")
    check(QUALIFICATION_VALUES == ("Exceptionnel", "Prioritaire", "Opportunité", "Ignorer"),
          "4 libellés exacts, dans l'ordre")
    # Tout résultat possible appartient au contrat de valeurs Notion.
    produced = {qualification(b) for b in (100, 90, 89, 80, 79, 70, 69, 0)}
    check(produced <= set(QUALIFICATION_VALUES), "tout résultat ∈ QUALIFICATION_VALUES")


# --- Seuil d'export (gate CRM centralisé) ------------------------------------


def test_should_export_threshold():
    print("test_should_export_threshold")
    check(EXPORT_MIN_BPS == 75, "seuil d'export centralisé relevé = 75 (plus sélectif)")
    check(should_export(75) is True, "75 (borne) -> exporté")
    check(should_export(85) is True, "85 -> exporté")
    check(should_export(100) is True, "100 -> exporté")
    check(should_export(74) is False, "74 (juste sous le seuil) -> non exporté")
    check(should_export(70) is False, "70 -> non exporté (ancien seuil, désormais sous la barre)")
    check(should_export(0) is False, "0 -> non exporté")


def test_should_export_non_numeric():
    print("test_should_export_non_numeric")
    check(should_export(None) is False, "None -> non exporté (jamais par défaut)")
    check(should_export("") is False, "'' -> non exporté")
    check(should_export("abc") is False, "texte -> non exporté")


def test_export_aligned_with_qualification():
    print("test_export_aligned_with_qualification")
    # Le seuil = plancher de la bande Opportunité : exporté SSI != Ignorer.
    for bps in (0, 50, 69, 70, 74, 75, 79, 80, 89, 90, 100):
        check(should_export(bps) == (qualification(bps) != "Ignorer"),
              f"BPS {bps} : export ⇔ qualification != Ignorer")


def main():
    for test in (
        test_bands_exceptionnel,
        test_bands_prioritaire,
        test_bands_opportunite,
        test_bands_ignorer,
        test_non_numeric_defaults_to_ignorer,
        test_values_contract,
        test_should_export_threshold,
        test_should_export_non_numeric,
        test_export_aligned_with_qualification,
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
