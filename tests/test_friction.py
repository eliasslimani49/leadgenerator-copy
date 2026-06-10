import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests du moteur d'irritants digitaux (friction.py).

Runner autonome, sans dépendance externe :  python3 test_friction.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.core.model import Prospect
from targetly.core.friction import (
    FRICTION_WEIGHTS,
    no_website,
    no_booking,
    website_outdated,
    no_cta,
    poor_ux,
    friction_flags,
    friction_score,
    assign_friction,
    besoins_from_flags,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def prospect(**overrides):
    """Prospect « propre » par défaut : aucun irritant ne se déclenche."""
    p = Prospect(
        telephone="01 23 45 67 89",
        has_website=True,
        has_booking=True,
        website_quality="good",
        website_freshness="fresh",
        cta_presence="present",
    )
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


def test_no_website():
    print("test_no_website")
    check(no_website(prospect(has_website=False)) is True, "pas de site -> no_website")
    check(no_website(prospect(has_website=True)) is False, "site présent -> pas no_website")


def test_no_booking():
    print("test_no_booking")
    check(no_booking(prospect(has_booking=False)) is True, "pas de résa -> no_booking")
    check(no_booking(prospect(has_booking=True)) is False, "résa présente -> pas no_booking")


def test_besoins_from_flags():
    print("test_besoins_from_flags")
    check(besoins_from_flags(["no_website", "no_booking"]) == ["Pas de site", "Pas de réservation en ligne"],
          "irritants mappés -> besoins Notion factuels (ordre stable)")
    check(besoins_from_flags(["no_cta", "poor_ux", "website_outdated"]) == [],
          "irritants sans mapping besoin -> aucun besoin (restent des signaux internes)")
    check(besoins_from_flags(["no_booking", "no_booking"]) == ["Pas de réservation en ligne"],
          "déduplication des besoins")
    check(besoins_from_flags([]) == [] and besoins_from_flags(None) == [], "vide / None -> []")


def test_website_outdated():
    print("test_website_outdated")
    check(website_outdated(prospect(has_website=True, website_freshness="outdated")) is True, "site vieillissant -> outdated")
    check(website_outdated(prospect(has_website=True, website_freshness="fresh")) is False, "site récent -> pas outdated")
    check(website_outdated(prospect(has_website=True, website_freshness="unknown")) is False, "freshness inconnue -> pas outdated")
    check(website_outdated(prospect(has_website=False, website_freshness="outdated")) is False, "pas de site -> pas outdated")


def test_no_cta():
    print("test_no_cta")
    check(no_cta(prospect(has_website=True, cta_presence="absent")) is True, "site sans CTA -> no_cta")
    check(no_cta(prospect(has_website=True, cta_presence="present")) is False, "CTA présent -> pas no_cta")
    check(no_cta(prospect(has_website=True, cta_presence="unknown")) is False, "CTA inconnu -> pas no_cta")
    check(no_cta(prospect(has_website=False, cta_presence="absent")) is False, "pas de site -> pas no_cta")


def test_poor_ux():
    print("test_poor_ux")
    check(poor_ux(prospect(has_website=True, website_quality="poor")) is True, "site médiocre -> poor_ux")
    check(poor_ux(prospect(has_website=True, website_quality="good")) is False, "bon site -> pas poor_ux")
    check(poor_ux(prospect(has_website=True, website_quality="unknown")) is False, "qualité inconnue -> pas poor_ux")
    check(poor_ux(prospect(has_website=False, website_quality="poor")) is False, "pas de site -> pas poor_ux")


def test_unknown_neutral():
    print("test_unknown_neutral")
    # Aucun signal site renseigné : seuls no_website + no_booking se déclenchent.
    p = Prospect(telephone="", has_website=False, has_booking=False)
    check(p.cta_presence == "unknown" and p.website_freshness == "unknown", "signaux par défaut = unknown")
    check(friction_flags(p) == ["no_website", "no_booking"], "unknown/absent -> aucun irritant site")
    check(friction_score(p) == FRICTION_WEIGHTS["no_website"] + FRICTION_WEIGHTS["no_booking"], "score = 3 + 2 = 5")


def test_offline_prospect():
    print("test_offline_prospect")
    # Fiche Google sans site, résa par téléphone : phone_only retiré -> 2 irritants.
    p = prospect(has_website=False, has_booking=False, telephone="01 23 45 67 89")
    check(friction_flags(p) == ["no_website", "no_booking"], "hors-ligne -> 2 irritants ordonnés")
    check(friction_score(p) == 3 + 2, "hors-ligne -> score 5 (plafond)")


def test_weighted_and_ordered():
    print("test_weighted_and_ordered")
    # Site présent mais tout est mauvais (no_website impossible avec site).
    p = prospect(
        has_website=True, has_booking=False, telephone="01",
        website_freshness="outdated", cta_presence="absent", website_quality="poor",
    )
    check(
        friction_flags(p) == ["no_booking", "website_outdated", "no_cta", "poor_ux"],
        "site dégradé -> 4 irritants dans l'ordre stable",
    )
    check(friction_score(p) == 2 + 1 + 1 + 1, "site dégradé -> score 5 (plafond, pondéré)")

    # Sous-ensemble : l'ordre stable est préservé.
    p2 = prospect(cta_presence="absent", website_quality="poor", website_freshness="fresh", has_booking=True)
    check(friction_flags(p2) == ["no_cta", "poor_ux"], "sous-ensemble -> ordre préservé")
    check(friction_score(p2) == 2, "sous-ensemble -> score 2")


def test_clean_prospect():
    print("test_clean_prospect")
    p = prospect()
    check(friction_flags(p) == [], "prospect propre -> aucun irritant")
    check(friction_score(p) == 0, "prospect propre -> score 0")


def test_assign_friction():
    print("test_assign_friction")
    p = prospect(has_website=False, has_booking=False, telephone="01")
    returned = assign_friction(p)
    check(returned is p, "assign_friction retourne le prospect")
    check(p.friction_flags == ["no_website", "no_booking"], "assign_friction écrit les flags")
    check(p.friction_score == 5, "assign_friction écrit le score")
    # Idempotent : pas d'accumulation si rappelé.
    assign_friction(p)
    check(p.friction_flags == ["no_website", "no_booking"], "assign_friction idempotent (flags)")
    check(p.friction_score == 5, "assign_friction idempotent (score)")


def main():
    for test in (
        test_no_website,
        test_no_booking,
        test_besoins_from_flags,
        test_website_outdated,
        test_no_cta,
        test_poor_ux,
        test_unknown_neutral,
        test_offline_prospect,
        test_weighted_and_ordered,
        test_clean_prospect,
        test_assign_friction,
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
