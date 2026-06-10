import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests de l'historique des campagnes (campaign_history.py, ADR-006).

Runner autonome, sans dépendance externe :  python3 test_campaign_history.py
Sort en code 1 si au moins un test échoue. Lecture seule sur tmpdir, AUCUN
réseau : la chaîne d'imports du module doit rester 100 % hors ligne.
"""

import json
import os
import sys
import tempfile

# Importé en PREMIER : le check hors-ligne ci-dessous doit précéder tout
# import qui tirerait requests (monitoring n'est importé que pour la parité).
from targetly.platform.services.campaign_history import (
    RUNS_LOG,
    aggregate_by_campaign,
    campaign_deltas,
    format_history,
    load_runs,
    normalize_run,
    recent_runs,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def run_line(keyword, city, *, total=10, inserted=2, rejected=3, cached=1, errors=0, ts=None):
    rec = {"keyword": keyword, "city": city, "total": total, "inserted": inserted,
           "rejected": rejected, "cached": cached, "errors": errors}
    if ts is not None:
        rec["ts"] = ts
    return json.dumps(rec, ensure_ascii=False)


def write_log(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_offline_imports_and_log_parity():
    print("test_offline_imports_and_log_parity")
    check("requests" not in sys.modules, "campaign_history n'importe jamais requests (hors ligne)")
    check("anthropic" not in sys.modules, "campaign_history n'importe jamais anthropic")
    from targetly.platform.services import monitoring  # importé APRÈS le check : sert uniquement à la parité
    check(RUNS_LOG == monitoring.RUNS_LOG,
          "RUNS_LOG identique à monitoring.RUNS_LOG (constante dupliquée alignée)")


def test_missing_or_empty_file():
    print("test_missing_or_empty_file")
    with tempfile.TemporaryDirectory() as d:
        runs, skipped = load_runs(os.path.join(d, "absent.jsonl"))
        check((runs, skipped) == ([], 0), "fichier absent -> ([], 0), jamais d'erreur")
        empty = os.path.join(d, "vide.jsonl")
        open(empty, "w").close()
        check(load_runs(empty) == ([], 0), "fichier vide -> ([], 0)")
        report = format_history([], 0)
        check("aucun run" in report, "rapport vide propre")


def test_corrupted_lines_skipped_and_counted():
    print("test_corrupted_lines_skipped_and_counted")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "runs.jsonl")
        write_log(path, [
            run_line("coiffeur", "Lyon", ts=1.0),
            "{ json cassé",
            '"pas un objet"',
            "",
            run_line("spa", "Paris", ts=2.0),
        ])
        runs, skipped = load_runs(path)
        check(len(runs) == 2, "2 runs valides conservés")
        check(skipped == 2, "2 lignes illisibles ignorées ET comptées (ligne vide non comptée)")
        check("2 ligne(s) illisible(s)" in format_history(runs, skipped),
              "lignes ignorées visibles dans le rapport")


def test_normalize_missing_fields():
    print("test_normalize_missing_fields")
    run = normalize_run({})  # format historique minimal : aucun champ
    check(run["keyword"] == "" and run["city"] == "" and run["ts"] is None,
          "champs absents -> défauts, jamais de KeyError")
    check(all(run[c] == 0 for c in ("total", "inserted", "rejected", "cached", "errors")),
          "compteurs absents -> 0")
    check(normalize_run({"total": "abc", "ts": "hier"})["total"] == 0,
          "valeurs non numériques -> défauts sûrs")


def test_recent_runs_order_and_limit():
    print("test_recent_runs_order_and_limit")
    runs = [normalize_run(json.loads(run_line(f"k{i}", "Lyon", ts=float(i)))) for i in range(5)]
    last = recent_runs(runs, limit=3)
    check([r["keyword"] for r in last] == ["k4", "k3", "k2"],
          "derniers N runs, plus récent d'abord (ordre du fichier)")
    check(recent_runs(runs, limit=0) == [], "limite 0 -> liste vide")


def test_aggregates():
    print("test_aggregates")
    lines = [
        run_line("coiffeur", "Lyon", total=10, inserted=2, errors=1, ts=1.0),
        run_line("coiffeur", "Lyon", total=6, inserted=3, errors=0, ts=2.0),
        run_line("spa", "Paris", total=0, inserted=0, ts=3.0),
    ]
    runs = [normalize_run(json.loads(l)) for l in lines]
    agg = aggregate_by_campaign(runs)
    lyon = agg[("coiffeur", "Lyon")]
    check(lyon["runs"] == 2 and lyon["total"] == 16 and lyon["inserted"] == 5,
          "agrégats cumulés par couple métier x zone")
    check(abs(lyon["insert_rate"] - 5 / 16) < 1e-9, "taux d'insertion = inserted/total")
    check(lyon["last_ts"] == 2.0, "dernier passage retenu")
    check(agg[("spa", "Paris")]["insert_rate"] is None,
          "total nul -> taux None (donnée absente ≠ taux 0)")


def test_deltas():
    print("test_deltas")
    lines = [
        run_line("coiffeur", "Lyon", total=10, inserted=2, errors=2, ts=1.0),
        run_line("spa", "Paris", total=5, inserted=1, ts=2.0),
        run_line("coiffeur", "Lyon", total=14, inserted=1, errors=0, ts=3.0),
    ]
    runs = [normalize_run(json.loads(l)) for l in lines]
    deltas = campaign_deltas(runs)
    check(len(deltas) == 1, "couple vu une seule fois -> pas de delta")
    d = deltas[0]
    check(d["keyword"] == "coiffeur" and d["delta_total"] == 4
          and d["delta_inserted"] == -1 and d["delta_errors"] == -2,
          "delta = dernier passage - avant-dernier (signés)")


def test_format_history_sections():
    print("test_format_history_sections")
    lines = [
        run_line("coiffeur", "Lyon", ts=1717000000.0),
        run_line("coiffeur", "Lyon", ts=1717100000.0),
    ]
    runs = [normalize_run(json.loads(l)) for l in lines]
    report = format_history(runs, 0)
    check("Derniers runs" in report, "section derniers runs")
    check("Agrégats par campagne" in report, "section agrégats")
    check("Évolution" in report, "section deltas présente quand >= 2 passages")
    check("coiffeur x Lyon" in report, "couple métier x zone lisible")
    solo = format_history(runs[:1], 0)
    check("Évolution" not in solo, "pas de section deltas avec un seul passage")


def main():
    for test in (
        test_offline_imports_and_log_parity,
        test_missing_or_empty_file,
        test_corrupted_lines_skipped_and_counted,
        test_normalize_missing_fields,
        test_recent_runs_order_and_limit,
        test_aggregates,
        test_deltas,
        test_format_history_sections,
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
