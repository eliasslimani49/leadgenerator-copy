"""Tests de la détection franchise / réseau (franchise.py).

Runner autonome, sans dépendance externe :  python3 test_franchise.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from franchise import (
    FRANCHISE_BRANDS,
    FRANCHISE_KEYWORDS,
    NETWORK_KEYWORDS,
    detect_franchise_signals,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_brand_in_name_confirms():
    print("test_brand_in_name_confirms")
    check(detect_franchise_signals("Basic Fit Lyon Part-Dieu", "") == ("confirmed", "confirmed"),
          "marque dans le nom -> franchise + réseau confirmés")
    check(detect_franchise_signals("Salon Franck Provost", "texte propre") == ("confirmed", "confirmed"),
          "enseigne coiffure dans le nom -> confirmés (prime sur le texte)")
    check(detect_franchise_signals("YVES ROCHER", None)[0] == "confirmed",
          "casse ignorée dans le nom")


def test_brand_in_text_suspects():
    print("test_brand_in_text_suspects")
    fr, net = detect_franchise_signals("Beauté Zen", "Membre du réseau Body Minute depuis 2020.")
    check((fr, net) == ("suspected", "suspected"), "marque dans le texte -> suspectés (pas confirmés)")


def test_keywords_axis_by_axis():
    print("test_keywords_axis_by_axis")
    fr, net = detect_franchise_signals("Studio Indé", "Devenez franchisé et rejoignez le réseau !")
    check(fr == "suspected", "vocabulaire franchise -> franchise suspectée")
    check(net == "suspected", "vocabulaire réseau -> réseau suspecté")
    fr2, net2 = detect_franchise_signals("Studio Indé", "Nous proposons des cours de pilates à Lyon.")
    check((fr2, net2) == ("no", "no"), "texte propre -> aucun signal (décideur local)")
    fr3, net3 = detect_franchise_signals("Coiffeur X", "Découvrez nos salons partout en France.")
    check(net3 == "suspected" and fr3 == "no", "vocabulaire réseau seul -> réseau suspecté, franchise non")


def test_unknown_when_no_data():
    print("test_unknown_when_no_data")
    check(detect_franchise_signals("", "") == ("unknown", "unknown"), "ni nom ni texte -> unknown (jamais 'no')")
    check(detect_franchise_signals("Cabinet sans site", None) == ("unknown", "unknown"),
          "nom neutre + texte absent -> unknown (donnée absente ≠ propre)")


def test_config_lists():
    print("test_config_lists")
    for lst, name in ((FRANCHISE_BRANDS, "FRANCHISE_BRANDS"),
                      (FRANCHISE_KEYWORDS, "FRANCHISE_KEYWORDS"),
                      (NETWORK_KEYWORDS, "NETWORK_KEYWORDS")):
        check(isinstance(lst, list) and lst, f"{name} non vide et configurable")
        check(all(s == s.lower() for s in lst), f"{name} en minuscules")


def main():
    for test in (
        test_brand_in_name_confirms,
        test_brand_in_text_suspects,
        test_keywords_axis_by_axis,
        test_unknown_when_no_data,
        test_config_lists,
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
