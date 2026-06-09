"""Tests du moteur de filtres éliminatoires (filters.py).

Runner autonome, sans dépendance externe :  python3 test_filters.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from model import Prospect
from filters import (
    is_incompatible_business,
    has_no_contact,
    has_insufficient_reviews,
    is_franchise_confirmed,
    is_network_confirmed,
    elimination_reasons,
    is_eliminated,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def eligible_prospect(**overrides):
    """Prospect éligible par défaut : aucune règle ne déclenche."""
    p = Prospect(
        nom="Studio Pilates Léa",
        telephone="01 23 45 67 89",
        raw_google_types=["gym", "point_of_interest"],
        raw_user_ratings_total=80,
        activity_signal="medium",
        franchise_signal="unknown",
    )
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


def test_incompatible_business():
    print("test_incompatible_business")
    check(is_incompatible_business(eligible_prospect(raw_google_types=["restaurant"])) is True, "restaurant -> incompatible")
    check(is_incompatible_business(eligible_prospect(raw_google_types=["clothing_store"])) is True, "clothing_store -> incompatible")
    check(is_incompatible_business(eligible_prospect(raw_google_types=["beauty_salon"])) is False, "beauty_salon -> compatible")
    check(is_incompatible_business(eligible_prospect(raw_google_types=[])) is False, "types vides -> compatible")
    for t in ("spa", "beauty_salon", "hair_care", "nail_salon", "gym",
              "physiotherapist", "doctor", "dentist", "health"):
        check(is_incompatible_business(eligible_prospect(raw_google_types=[t])) is False, f"protégé: {t} -> compatible")
    check(is_incompatible_business(eligible_prospect(business_type="autre", raw_google_types=[])) is False, "business_type autre seul -> compatible")


def test_no_contact():
    print("test_no_contact")
    check(has_no_contact(eligible_prospect(telephone="", email="", instagram="", facebook="")) is True, "4 canaux vides -> no_contact")
    check(has_no_contact(eligible_prospect(telephone="01", email="", instagram="", facebook="")) is False, "téléphone seul -> contact ok")
    check(has_no_contact(eligible_prospect(telephone="", email="a@b.com", instagram="", facebook="")) is False, "email seul -> contact ok")
    check(has_no_contact(eligible_prospect(telephone="", email="", instagram="ig", facebook="")) is False, "instagram seul -> contact ok")
    check(has_no_contact(eligible_prospect(telephone="", email="", instagram="", facebook="fb")) is False, "facebook seul -> contact ok")
    check(has_no_contact(eligible_prospect(telephone="", email="", instagram="", facebook="", site_web="http://x")) is True, "site web seul -> no_contact")


def test_reviews():
    print("test_reviews")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=10, activity_signal="low")) is True, "10 avis connus -> insuffisant")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=19, activity_signal="low")) is True, "19 avis connus -> insuffisant")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=20, activity_signal="medium")) is False, "20 avis connus -> suffisant")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=200, activity_signal="high")) is False, "200 avis connus -> suffisant")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=0, activity_signal="low")) is True, "0 avis connu -> insuffisant")
    check(has_insufficient_reviews(eligible_prospect(raw_user_ratings_total=0, activity_signal="unknown")) is False, "avis inconnus -> pas de rejet")


def test_franchise():
    print("test_franchise")
    check(is_franchise_confirmed(eligible_prospect(franchise_signal="confirmed")) is True, "confirmed -> franchise")
    check(is_franchise_confirmed(eligible_prospect(franchise_signal="unknown")) is False, "unknown -> pas de rejet")
    check(is_franchise_confirmed(eligible_prospect(franchise_signal="suspected")) is False, "suspected -> pas de rejet")
    check(is_franchise_confirmed(eligible_prospect(franchise_signal="no")) is False, "no -> pas de rejet")


def test_network():
    print("test_network")
    check(is_network_confirmed(eligible_prospect(network_signal="confirmed")) is True, "confirmed -> réseau")
    check(is_network_confirmed(eligible_prospect(network_signal="unknown")) is False, "unknown -> pas de rejet")
    check(is_network_confirmed(eligible_prospect(network_signal="suspected")) is False, "suspected -> pas de rejet (BPS seulement)")
    check(is_network_confirmed(eligible_prospect(network_signal="no")) is False, "no -> pas de rejet")


def test_aggregate():
    print("test_aggregate")
    check(elimination_reasons(eligible_prospect()) == [], "prospect éligible -> aucune raison")
    check(is_eliminated(eligible_prospect()) is False, "prospect éligible -> non éliminé")

    bad = eligible_prospect(
        raw_google_types=["restaurant"],
        telephone="", email="", instagram="", facebook="",
        raw_user_ratings_total=5, activity_signal="low",
        franchise_signal="confirmed",
    )
    check(set(elimination_reasons(bad)) == {"incompatible_business", "no_contact_channel", "reviews_below_min", "franchise_confirmed"}, "multi-rejet -> 4 codes")
    check(is_eliminated(bad) is True, "multi-rejet -> éliminé")
    check("franchise_confirmed" not in elimination_reasons(eligible_prospect(franchise_signal="unknown")), "franchise unknown -> pas dans les raisons")
    # Réseau confirmé -> code dédié, sans bloquer les autres règles.
    check("network_confirmed" in elimination_reasons(eligible_prospect(network_signal="confirmed")), "réseau confirmé -> code network_confirmed")
    check("network_confirmed" not in elimination_reasons(eligible_prospect(network_signal="suspected")), "réseau suspecté -> pas dans les raisons")


def main():
    for test in (
        test_incompatible_business,
        test_no_contact,
        test_reviews,
        test_franchise,
        test_network,
        test_aggregate,
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
