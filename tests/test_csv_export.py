import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests de l'export CSV V1 (csv_export.py).

Runner autonome, sans dépendance externe :  python3 test_csv_export.py
Sort en code 1 si au moins un test échoue. Aucune I/O hors tmpdir, AUCUN
réseau : le module et toute sa chaîne d'imports doivent rester hors ligne.
"""

import csv
import os
import sys
import tempfile

from targetly.platform.services.csv_export import (
    CSV_COLUMNS,
    eligible_prospects,
    export_csv,
    format_export_report,
    prospect_row,
)
from targetly.core.model import Prospect
from targetly.core.segmentation import EXPORT_MIN_BPS

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def make_prospect(**overrides):
    """Prospect minimal valide ; bps/champs surchargés par test."""
    base = dict(
        nom="Salon Test", ville="Lyon", activites="Coiffure",
        telephone="0102030405", email="salon@test.fr",
        place_id="pid-1", bps=EXPORT_MIN_BPS,
        friction_score=3, friction_flags=["no_booking"],
        besoins=["Pas de clients"], booking_software="Aucun",
        notes="Notes factuelles.",
    )
    base.update(overrides)
    return Prospect(**base)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_no_network_imports():
    print("test_no_network_imports")
    check("requests" not in sys.modules, "csv_export n'importe jamais requests (hors ligne)")
    check("anthropic" not in sys.modules, "csv_export n'importe jamais anthropic")


def test_nominal_export():
    print("test_nominal_export")
    prospects = [
        make_prospect(place_id="pid-1", nom="A", bps=EXPORT_MIN_BPS + 10),
        make_prospect(place_id="pid-2", nom="B", bps=EXPORT_MIN_BPS),
        make_prospect(place_id="pid-3", nom="C", bps=EXPORT_MIN_BPS - 1),
    ]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.csv")
        result = export_csv(prospects, path)
        check(result.analyzed == 3, "analysés = 3")
        check(result.eligible == 2, "éligibles = 2 (un sous le seuil)")
        check(result.exported == 2, "exportés = 2")
        rows = read_csv(path)
        check(len(rows) == 3, "fichier = en-tête + 2 lignes")
        check(rows[1][1] == "A" and rows[2][1] == "B", "tri BPS décroissant")
        check("exportés" in format_export_report(result), "bilan lisible")


def test_should_export_threshold():
    print("test_should_export_threshold")
    at = make_prospect(place_id="p-at", bps=EXPORT_MIN_BPS)
    below = make_prospect(place_id="p-below", bps=EXPORT_MIN_BPS - 1)
    zero = make_prospect(place_id="p-zero", bps=0)
    kept = eligible_prospects([at, below, zero])
    check([p.place_id for p in kept] == ["p-at"],
          f"seuil {EXPORT_MIN_BPS} inclus, {EXPORT_MIN_BPS - 1} et 0 exclus (should_export)")


def test_dry_run_writes_nothing():
    print("test_dry_run_writes_nothing")
    prospects = [make_prospect(bps=EXPORT_MIN_BPS + 5)]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.csv")
        result = export_csv(prospects, path, dry_run=True)
        check(not os.path.exists(path), "dry-run : aucun fichier écrit")
        check(result.eligible == 1 and result.exported == 0,
              "dry-run : éligibles comptés, exportés = 0")
        check("dry-run" in format_export_report(result), "bilan marqué dry-run")


def test_columns_structure():
    print("test_columns_structure")
    p = make_prospect(
        bps=EXPORT_MIN_BPS + 5,
        friction_flags=["no_booking", "no_cta"],
        bps_breakdown={"business_maturity": {"score": 0.8, "known": True}},
        booking_details="Réservation par téléphone uniquement.",
    )
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.csv")
        export_csv([p], path)
        rows = read_csv(path)
        check(rows[0] == list(CSV_COLUMNS), "en-tête = colonnes contractuelles, ordre exact")
        line = dict(zip(rows[0], rows[1]))
        check(line["qualification"] != "" and line["bps"] == str(EXPORT_MIN_BPS + 5),
              "bps et qualification remplis")
        check(line["niveau_structuration"] == "80", "business_maturity 0.8 -> 80")
        check(line["friction_flags"] == "no_booking | no_cta", "flags joints par ' | '")
        check(line["booking"] == "Réservation par téléphone uniquement.",
              "booking_details prime sur booking_software")
        check(line["besoins"].startswith("Pas de réservation en ligne"),
              "irritants factuels en tête des besoins")


def test_unknown_structuration_stays_empty():
    print("test_unknown_structuration_stays_empty")
    p = make_prospect(bps=EXPORT_MIN_BPS, bps_breakdown={
        "business_maturity": {"score": None, "known": False},
    })
    check(prospect_row(p)["niveau_structuration"] == "",
          "dimension inconnue -> cellule vide, jamais 0")
    check(prospect_row(make_prospect(bps_breakdown={}))["niveau_structuration"] == "",
          "breakdown absent -> cellule vide")


def test_idempotent_and_dedup():
    print("test_idempotent_and_dedup")
    dup_low = make_prospect(place_id="dup", nom="Doublon", bps=EXPORT_MIN_BPS)
    dup_high = make_prospect(place_id="dup", nom="Doublon", bps=EXPORT_MIN_BPS + 8)
    other = make_prospect(place_id="autre", nom="Autre", bps=EXPORT_MIN_BPS + 1)
    prospects = [dup_low, other, dup_high]
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = os.path.join(d, "a.csv"), os.path.join(d, "b.csv")
        r1 = export_csv(prospects, p1)
        export_csv(list(reversed(prospects)), p2)
        with open(p1, "rb") as f1, open(p2, "rb") as f2:
            check(f1.read() == f2.read(),
                  "même contenu quel que soit l'ordre d'entrée (octet par octet)")
        check(r1.eligible == 2, "doublon place_id dédupliqué")
        rows = read_csv(p1)
        line = dict(zip(rows[0], rows[1]))
        check(line["bps"] == str(EXPORT_MIN_BPS + 8), "le meilleur BPS gagne au dédoublonnage")


def test_empty_and_none_inputs():
    print("test_empty_and_none_inputs")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.csv")
        result = export_csv([], path)
        check(result == (0, 0, 0, path, False), "liste vide -> bilan à zéro, pas une erreur")
        rows = read_csv(path)
        check(rows == [list(CSV_COLUMNS)], "fichier avec en-tête seul")
        check(export_csv(None, os.path.join(d, "n.csv")).analyzed == 0, "None toléré")


def test_write_failure_visible():
    print("test_write_failure_visible")
    with tempfile.TemporaryDirectory() as d:
        bad_path = os.path.join(d, "inexistant", "out.csv")
        try:
            export_csv([make_prospect()], bad_path)
            check(False, "chemin invalide -> OSError attendue")
        except OSError:
            check(True, "échec d'écriture VISIBLE (OSError remontée, jamais avalée)")


def main():
    for test in (
        test_no_network_imports,
        test_nominal_export,
        test_should_export_threshold,
        test_dry_run_writes_nothing,
        test_columns_structure,
        test_unknown_structuration_stays_empty,
        test_idempotent_and_dedup,
        test_empty_and_none_inputs,
        test_write_failure_visible,
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
