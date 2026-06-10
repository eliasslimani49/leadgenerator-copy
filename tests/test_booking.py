"""Tests de la détection d'outil de réservation (booking.py).

Runner autonome, sans dépendance externe :  python3 test_booking.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from booking import BOOKING_PROVIDERS, detect_booking_provider

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_detects_known_providers():
    print("test_detects_known_providers")
    check(detect_booking_provider('<a href="https://www.planity.com/x">RDV</a>') == "Planity",
          "lien Planity -> Planity")
    check(detect_booking_provider('<iframe src="https://widget.treatwell.fr/..."></iframe>') == "Treatwell",
          "iframe Treatwell -> Treatwell")
    check(detect_booking_provider('<script src="https://calendly.com/assets/x.js"></script>') == "Calendly",
          "script Calendly -> Calendly")
    check(detect_booking_provider('<a href="https://www.doctolib.fr/cabinet">Prendre RDV</a>') == "Doctolib",
          "lien Doctolib -> Doctolib")


def test_case_insensitive_and_raw_html():
    print("test_case_insensitive_and_raw_html")
    check(detect_booking_provider('<A HREF="HTTPS://FRESHA.COM/abc">') == "Fresha",
          "casse ignorée (HTML majuscule) -> Fresha")
    # La signature vit dans le HREF/SRC, pas dans le texte visible.
    check(detect_booking_provider('<a href="https://simplybook.me/v2/">Réserver</a>') == "SimplyBook",
          "détection sur l'attribut href (pas le texte)")


def test_no_signature_returns_none():
    print("test_no_signature_returns_none")
    check(detect_booking_provider("") is None, "HTML vide -> None")
    check(detect_booking_provider(None) is None, "None -> None")
    check(detect_booking_provider("<html><body>Contactez-nous au 01 23 45 67 89</body></html>") is None,
          "aucune signature -> None (jamais d'invention)")
    # Un site qui parle de réservation sans widget connu ne doit PAS être détecté.
    check(detect_booking_provider("<p>Réservation par téléphone uniquement</p>") is None,
          "vocabulaire 'réservation' sans widget -> None")


def test_first_match_wins_and_config():
    print("test_first_match_wins_and_config")
    # Déterminisme : ordre d'insertion du dict (Planity avant Calendly).
    html = '<a href="planity.com"></a><a href="calendly.com"></a>'
    check(detect_booking_provider(html) == "Planity", "1er fournisseur trouvé gagne (déterministe)")
    check(isinstance(BOOKING_PROVIDERS, dict) and len(BOOKING_PROVIDERS) >= 10,
          "table de fournisseurs configurable et fournie")
    for provider, needles in BOOKING_PROVIDERS.items():
        check(all(n == n.lower() for n in needles), f"signatures en minuscules pour {provider}")


def main():
    for test in (
        test_detects_known_providers,
        test_case_insensitive_and_raw_html,
        test_no_signature_returns_none,
        test_first_match_wins_and_config,
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
