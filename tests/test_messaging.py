import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests du messaging par bande BPS (messaging.py).

Runner autonome, sans dépendance externe :  python3 test_messaging.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.core.messaging import (
    ANGLE_BY_QUALIFICATION, message_angle, response_rates,
    messenger_message, messenger_angle, confidence_level,
    message_quality_score, message_quality_label, build_messenger_payload,
    CONFIDENCE_LEVELS, MESSAGE_ANGLES,
)
from targetly.core.model import Prospect

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_message_angle_by_band():
    print("test_message_angle_by_band")
    check(message_angle(92) == ("Exceptionnel", ANGLE_BY_QUALIFICATION["Exceptionnel"]), "92 -> bande Exceptionnel + angle")
    check(message_angle(85)[0] == "Prioritaire", "85 -> Prioritaire")
    check(message_angle(75)[0] == "Opportunité", "75 -> Opportunité")
    check(message_angle(50) == ("Ignorer", "aucun"), "50 -> Ignorer + angle 'aucun' (hors démarchage)")
    check(message_angle(None) == ("Ignorer", "aucun"), "BPS absent -> Ignorer (jamais promu)")


def test_response_rates_by_band():
    print("test_response_rates_by_band")
    outcomes = {
        "a": {"outcome": "won", "bps": 92},        # Exceptionnel : contacté + répondu
        "b": {"outcome": "replied", "bps": 85},    # Prioritaire : contacté + répondu
        "c": {"outcome": "contacted", "bps": 85},  # Prioritaire : contacté, pas de réponse
        "d": {"outcome": "lost", "bps": 75},       # Opportunité : contacté, pas compté répondu
        "e": {"outcome": "none", "bps": 75},       # Opportunité : PAS contacté -> exclu
        "f": {"outcome": "meeting", "bps": 50},    # Ignorer : contacté + répondu
    }
    rates = response_rates(outcomes)
    check(rates["Exceptionnel"] == {"contacted": 1, "responded": 1, "rate": 1.0}, "Exceptionnel : 1/1 = 1.0")
    check(rates["Prioritaire"] == {"contacted": 2, "responded": 1, "rate": 0.5}, "Prioritaire : 1/2 = 0.5")
    check(rates["Opportunité"]["contacted"] == 1, "Opportunité : 'none' exclu du contacté")
    check(rates["Opportunité"]["responded"] == 0 and rates["Opportunité"]["rate"] == 0.0,
          "Opportunité : perdu = contacté non répondu -> taux 0.0")
    check(rates["Ignorer"]["rate"] == 1.0, "Ignorer : 1/1 (mesuré même hors démarchage cible)")


def test_response_rates_empty():
    print("test_response_rates_empty")
    rates = response_rates({})
    check(all(v["contacted"] == 0 and v["rate"] is None for v in rates.values()),
          "aucun contact -> rate None partout (donnée absente ≠ taux 0)")
    check(response_rates(None) is not None, "None toléré (jamais d'exception)")


# --- Modèle de message Messenger (génération différenciée) -------------------


def _p(**kw):
    """Prospect minimal de test : nom non vide, le reste surchargé au besoin."""
    return Prospect(nom="X", **kw)


# Formulations BANNIES (trop commerciales / jargon SaaS / IA générique).
BANNED = (
    "optimiser votre gestion opérationnelle",
    "solution innovante",
    "présence digitale optimisée",
    "transformation digitale",
    "gain de productivité",
)

# Un prospect représentatif par angle (couvre les 6 branches de messenger_angle).
ANGLE_FIXTURES = {
    "image_pro": _p(business_type="photographie", has_website=False),
    "reservation": _p(business_type="coach_sportif", has_website=True, has_booking=False),
    "simplification": _p(business_type="beaute", has_website=True, has_booking=True,
                         booking_quality="basic", booking_software="Calendly"),
    "centralisation": _p(business_type="therapie", has_website=True, has_booking=True,
                         booking_quality="advanced", telephone="01 02 03 04 05",
                         facebook="https://fb.com/x"),
    "avis_google": _p(business_type="bien_etre", has_website=True, has_booking=True,
                      booking_quality="advanced", raw_rating=4.8, raw_user_ratings_total=40),
    "ecoute_terrain": _p(business_type="coach_business", has_website=True, has_booking=True,
                         booking_quality="advanced"),
}


def test_angle_detection():
    print("test_angle_detection")
    for expected, prospect in ANGLE_FIXTURES.items():
        check(messenger_angle(prospect) == expected, f"angle '{expected}' détecté")
    # Site injoignable (404) -> image_pro (priorité à la friction site).
    p404 = _p(has_website=True)
    p404.website_unreachable = True
    check(messenger_angle(p404) == "image_pro", "site injoignable (404) -> image_pro")
    # Site daté -> image_pro également.
    check(messenger_angle(_p(has_website=True, website_freshness="outdated")) == "image_pro",
          "site daté -> image_pro")


def test_confidence_levels():
    print("test_confidence_levels")
    check(confidence_level(_p(has_website=False)) == "HIGH", "site absent -> HIGH (fait direct)")
    p404 = _p(has_website=True)
    p404.website_unreachable = True
    check(confidence_level(p404) == "MEDIUM", "site injoignable -> MEDIUM (indice faillible)")
    check(confidence_level(ANGLE_FIXTURES["simplification"]) == "HIGH", "outil connu (Calendly) -> HIGH")
    inconnu = _p(has_website=True, has_booking=True, booking_quality="basic", booking_software="OutilMaison")
    check(confidence_level(inconnu) == "MEDIUM", "simplification sans outil connu -> MEDIUM")
    check(confidence_level(ANGLE_FIXTURES["avis_google"]) == "HIGH", "avis chiffrés -> HIGH")
    check(confidence_level(ANGLE_FIXTURES["ecoute_terrain"]) == "LOW", "écoute pure -> LOW")
    check(confidence_level(ANGLE_FIXTURES["reservation"]) == "MEDIUM", "réservation -> MEDIUM (défaut prudent)")


def test_messenger_observations():
    print("test_messenger_observations")
    # Site absent.
    m = messenger_message(_p(business_type="therapie", has_website=False))
    check("Je n'ai pas trouvé de site" in m, "site absent : observation dédiée prudente")
    check("thérapeutes" in m, "métier mappé au pluriel (therapie -> thérapeutes)")
    # Site 404 / injoignable.
    p404 = _p(has_website=True)
    p404.website_unreachable = True
    check("ne s'est pas affiché correctement" in messenger_message(p404),
          "site 404 : formulation prudente ('sauf erreur de ma part')")
    # Calendly détecté mais créneaux non visibles (non paramétré).
    mc = messenger_message(ANGLE_FIXTURES["simplification"])
    check("Calendly" in mc and "créneaux disponibles" in mc, "Calendly nommé + prudence sur les dispos")
    # Bons avis Google.
    check("très bons retours" in messenger_message(ANGLE_FIXTURES["avis_google"]),
          "bons avis Google : observation valorisante factuelle")
    # Prospect déjà digitalisé (réservation avancée, rien d'autre) -> écoute, sans critique.
    md = messenger_message(ANGLE_FIXTURES["ecoute_terrain"])
    check("Je découvre tout juste votre activité" in md, "déjà digitalisé : angle écoute, aucune critique")
    check("pénible" not in md and "problème" not in md, "aucun jugement négatif sur un prospect digitalisé")


def test_no_invention_unknown_tool():
    print("test_no_invention_unknown_tool")
    p = _p(business_type="coach_sportif", has_website=True, has_booking=True,
           booking_quality="basic", booking_software="OutilMaison")
    m = messenger_message(p)
    check("OutilMaison" not in m, "outil inconnu JAMAIS nommé (aucune invention)")
    check("système de réservation" in m, "réservation évoquée prudemment, sans nommer l'outil")
    check("vous n'avez pas" not in m.lower(), "jamais d'affirmation d'absence non vérifiable")


def test_messenger_greeting_and_ville():
    print("test_messenger_greeting_and_ville")
    base = messenger_message(_p(business_type="autre"))
    check(base.startswith("Bonjour,"), "salutation neutre par défaut (pas de prénom inventé du nom)")
    check("indépendants" in base, "métier inconnu/'autre' -> 'indépendants' (générique crédible)")
    p = _p(business_type="coach_sportif", ville="Lyon")
    p.prenom = "Paul"  # prénom explicitement fourni
    m = messenger_message(p)
    check(m.startswith("Bonjour Paul,"), "prénom utilisé SEULEMENT s'il est explicitement fourni")
    check("autour de Lyon" in m, "ville réelle intégrée prudemment")


def test_quality_score_and_label():
    print("test_quality_score_and_label")
    # Profil riche : métier connu + ville + friction actionnable + canal + outil connu + avis + HIGH.
    riche = _p(business_type="coach_sportif", ville="Lyon", has_website=True, has_booking=True,
               booking_quality="basic", booking_software="Calendly",
               telephone="01 02 03 04 05", raw_rating=4.8, raw_user_ratings_total=40)
    check(message_quality_score(riche) == 100, "profil riche -> score plafonné à 100")
    check(message_quality_label(message_quality_score(riche)) == "Prêt à envoyer", "100 -> Prêt à envoyer")
    # Profil intermédiaire : métier connu (20) + image_pro (20) + avis>=20 (10) + HIGH (10) = 60.
    inter = _p(business_type="coach_sportif", has_website=False,
               raw_rating=4.0, raw_user_ratings_total=30)
    check(message_quality_score(inter) == 60, "profil intermédiaire -> 60")
    check(message_quality_label(message_quality_score(inter)) == "À relire", "60 -> À relire")
    # Profil pauvre : métier inconnu, déjà digitalisé, aucun signal exploitable = 0.
    pauvre = _p(business_type="autre", has_website=True, has_booking=True, booking_quality="advanced")
    check(message_quality_score(pauvre) == 0, "profil pauvre -> 0")
    check(message_quality_label(message_quality_score(pauvre)) == "À personnaliser manuellement",
          "0 -> À personnaliser manuellement")
    # Bornes du libellé (le score ORIENTE le triage, il ne bloque jamais la génération).
    check(message_quality_label(75) == "Prêt à envoyer" and message_quality_label(74) == "À relire",
          "borne 75 : Prêt / À relire")
    check(message_quality_label(50) == "À relire" and message_quality_label(49) == "À personnaliser manuellement",
          "borne 50 : À relire / À personnaliser")


def test_messenger_deterministic_and_not_commercial():
    print("test_messenger_deterministic_and_not_commercial")
    p = _p(business_type="photographie", has_website=True, has_booking=False, ville="Nice")
    check(messenger_message(p) == messenger_message(p), "déterministe : même entrée -> même sortie")
    # Sur TOUS les angles : aucune formulation bannie, ton écoute terrain non commercial.
    for angle, prospect in ANGLE_FIXTURES.items():
        m = messenger_message(prospect).lower()
        check(not any(b in m for b in BANNED), f"{angle} : aucune formulation trop commerciale")
        check("sans démarche commerciale" in m, f"{angle} : mention explicite 'sans démarche commerciale'")
        check("écoute terrain" in m, f"{angle} : ton écoute terrain")


def test_payload_shape():
    print("test_payload_shape")
    p = ANGLE_FIXTURES["simplification"]
    payload = build_messenger_payload(p)
    check(set(payload) == {"message", "angle", "confidence", "quality_score", "quality_label"},
          "payload : clés attendues exactes")
    check(payload["message"] == messenger_message(p), "payload.message == messenger_message")
    check(payload["angle"] in MESSAGE_ANGLES, "payload.angle dans MESSAGE_ANGLES")
    check(payload["confidence"] in CONFIDENCE_LEVELS, "payload.confidence dans CONFIDENCE_LEVELS")
    check(payload["quality_label"] == message_quality_label(payload["quality_score"]),
          "payload.quality_label cohérent avec quality_score")


def main():
    for test in (
        test_message_angle_by_band,
        test_response_rates_by_band,
        test_response_rates_empty,
        test_angle_detection,
        test_confidence_levels,
        test_messenger_observations,
        test_no_invention_unknown_tool,
        test_messenger_greeting_and_ville,
        test_quality_score_and_label,
        test_messenger_deterministic_and_not_commercial,
        test_payload_shape,
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
