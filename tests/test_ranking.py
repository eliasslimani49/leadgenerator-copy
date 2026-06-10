import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests du moteur de classement (ranking.py).

Runner autonome, sans dépendance externe :  python3 test_ranking.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.core.model import Prospect
from targetly.core.ranking import (
    MIN_BPS,
    TOP_STRICT,
    TOP_EXTENDED,
    rank,
    top_n,
    select_elite,
    group_by_business_type,
    group_by_city,
    top_by_group,
    compute_stats,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def make(nom, bps, *, friction=0, business_type="autre", ville=""):
    return Prospect(nom=nom, bps=bps, friction_score=friction,
                    business_type=business_type, ville=ville)


def noms(prospects):
    return [p.nom for p in prospects]


# --- rank : tri déterministe, départages -------------------------------------


def test_rank_order():
    print("test_rank_order")
    a = make("A", 90)
    b = make("B", 70)
    c = make("C", 95)
    ordered = rank([a, b, c])
    check(noms(ordered) == ["C", "A", "B"], "tri par BPS décroissant")


def test_rank_tiebreak_friction():
    print("test_rank_tiebreak_friction")
    low = make("LowFriction", 80, friction=1)
    high = make("HighFriction", 80, friction=5)
    ordered = rank([low, high])
    check(noms(ordered) == ["HighFriction", "LowFriction"], "à BPS égal, friction haute devant")


def test_rank_tiebreak_nom():
    print("test_rank_tiebreak_nom")
    z = make("Zeta", 80, friction=2)
    a = make("Alpha", 80, friction=2)
    ordered = rank([z, a])
    check(noms(ordered) == ["Alpha", "Zeta"], "à BPS+friction égaux, ordre alphabétique du nom")


def test_rank_pure():
    print("test_rank_pure")
    src = [make("A", 90), make("B", 95)]
    rank(src)
    check(noms(src) == ["A", "B"], "rank ne modifie pas la liste source")
    check(rank([]) == [], "rank([]) -> []")


def test_determinism():
    print("test_determinism")
    src = [make("A", 80, friction=2), make("B", 80, friction=2), make("C", 90)]
    check(noms(rank(src)) == noms(rank(src)), "même entrée -> même classement")


# --- top_n : pas de plancher -------------------------------------------------


def test_top_n():
    print("test_top_n")
    src = [make(str(i), i * 10) for i in range(1, 8)]  # BPS 10..70
    top = top_n(src, 3)
    check(noms(top) == ["7", "6", "5"], "top_n prend les n meilleurs")
    check(len(top_n(src, 100)) == 7, "top_n borné par la taille de la liste")
    check(top_n(src, 0) == [], "top_n(_, 0) -> []")
    # top_n n'applique aucun plancher : un prospect faible peut sortir.
    weak = [make("faible", 10), make("nul", 5)]
    check(noms(top_n(weak, 1)) == ["faible"], "top_n ignore le plancher BPS")


# --- select_elite : plancher MIN_BPS avant la coupe --------------------------


def test_select_elite_floor():
    print("test_select_elite_floor")
    src = [make("excellent", 90), make("limite", MIN_BPS), make("juste_sous", MIN_BPS - 1), make("faible", 30)]
    elite = select_elite(src)
    check(noms(elite) == ["excellent", "limite"], "élite = BPS >= plancher MIN_BPS, classés")
    check(all(p.bps >= MIN_BPS for p in elite), "aucun prospect sous le plancher dans l'élite")


def test_select_elite_limit():
    print("test_select_elite_limit")
    src = [make(str(i), 70 + i) for i in range(15)]  # 15 prospects tous éligibles
    check(len(select_elite(src, limit=TOP_EXTENDED)) == TOP_EXTENDED, "élite plafonnée à TOP_EXTENDED")
    check(len(select_elite(src, limit=TOP_STRICT)) == TOP_STRICT, "élite plafonnée à TOP_STRICT")


def test_select_elite_empty_when_all_weak():
    print("test_select_elite_empty_when_all_weak")
    src = [make("a", 50), make("b", 69), make("c", 10)]
    check(select_elite(src) == [], "aucun éligible -> élite vide (aucun faible remonté)")


def test_select_elite_custom_floor():
    print("test_select_elite_custom_floor")
    src = [make("a", 60), make("b", 40)]
    check(noms(select_elite(src, min_bps=50)) == ["a"], "plancher configurable")


# --- Regroupements -----------------------------------------------------------


def test_group_by_business_type():
    print("test_group_by_business_type")
    src = [
        make("coach1", 80, business_type="coach_sportif"),
        make("coach2", 95, business_type="coach_sportif"),
        make("beaute1", 60, business_type="beaute"),
    ]
    groups = group_by_business_type(src)
    check(set(groups.keys()) == {"coach_sportif", "beaute"}, "clés = types métier présents")
    check(noms(groups["coach_sportif"]) == ["coach2", "coach1"], "groupe métier classé par BPS")


def test_group_by_city():
    print("test_group_by_city")
    src = [
        make("p1", 80, ville="Lyon"),
        make("p2", 90, ville="Lyon"),
        make("p3", 70, ville=""),
    ]
    groups = group_by_city(src)
    check(noms(groups["Lyon"]) == ["p2", "p1"], "groupe ville classé par BPS")
    check("" in groups, "ville non renseignée regroupée sous ''")


def test_top_by_group():
    print("test_top_by_group")
    src = [
        make("c1", 90, business_type="coach_sportif"),
        make("c2", 75, business_type="coach_sportif"),
        make("c3", 50, business_type="coach_sportif"),  # sous le plancher
        make("b1", 80, business_type="beaute"),
        make("b2", 40, business_type="beaute"),          # sous le plancher
    ]
    top = top_by_group(src, lambda p: p.business_type, 5)
    check(noms(top["coach_sportif"]) == ["c1", "c2"], "top par groupe applique le plancher")
    check(noms(top["beaute"]) == ["b1"], "groupe beaute : seul l'éligible retenu")


def test_top_by_group_limit():
    print("test_top_by_group_limit")
    src = [make(f"c{i}", MIN_BPS + i, business_type="coach_sportif") for i in range(6)]
    top = top_by_group(src, lambda p: p.business_type, 2)
    check(len(top["coach_sportif"]) == 2, "limite n respectée par groupe")


# --- Statistiques ------------------------------------------------------------


def test_compute_stats():
    print("test_compute_stats")
    src = [
        make("a", 90, business_type="coach_sportif", ville="Lyon"),
        make("b", 70, business_type="coach_sportif", ville="Lyon"),
        make("c", 50, business_type="beaute", ville="Paris"),
        make("d", 30, business_type="beaute", ville=""),
    ]
    stats = compute_stats(src)
    check(stats["total"] == 4, "total = 4")
    check(stats["elite_count"] == 1, "elite_count = 1 (>= MIN_BPS, seuil relevé à 75)")
    check(stats["bps_min"] == 30 and stats["bps_max"] == 90, "min/max BPS")
    check(stats["bps_avg"] == 60.0, "moyenne BPS = 60.0")
    check(stats["bps_median"] == 60.0, "médiane BPS = 60.0")
    check(stats["by_business_type"] == {"coach_sportif": 2, "beaute": 2}, "comptage par métier")
    check(stats["by_city"] == {"Lyon": 2, "Paris": 1, "": 1}, "comptage par ville")
    check(stats["min_bps"] == MIN_BPS, "plancher rappelé dans les stats")


def test_compute_stats_empty():
    print("test_compute_stats_empty")
    stats = compute_stats([])
    check(stats["total"] == 0, "liste vide : total 0")
    check(stats["elite_count"] == 0, "liste vide : elite_count 0")
    check(stats["bps_min"] is None and stats["bps_max"] is None, "liste vide : min/max None")
    check(stats["bps_avg"] is None and stats["bps_median"] is None, "liste vide : avg/median None")
    check(stats["by_business_type"] == {} and stats["by_city"] == {}, "liste vide : comptages vides")


def main():
    for test in (
        test_rank_order,
        test_rank_tiebreak_friction,
        test_rank_tiebreak_nom,
        test_rank_pure,
        test_determinism,
        test_top_n,
        test_select_elite_floor,
        test_select_elite_limit,
        test_select_elite_empty_when_all_weak,
        test_select_elite_custom_floor,
        test_group_by_business_type,
        test_group_by_city,
        test_top_by_group,
        test_top_by_group_limit,
        test_compute_stats,
        test_compute_stats_empty,
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
