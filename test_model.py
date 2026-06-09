"""Tests du socle de données prospect (model.py).

Runner autonome, sans dépendance externe :  python3 test_model.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from model import (
    Prospect,
    build_prospect,
    validate_prospect,
    sanitize_choice,
    derive_business_type,
    derive_business_type_from_keyword,
    derive_activity_signal,
    derive_business_size,
    derive_ticket_size,
    derive_decision_complexity,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def no_none(d):
    return all(v is not None for v in d.values())


# --- Defaults ---------------------------------------------------------------

def test_defaults():
    print("test_defaults")
    p = Prospect()
    check(no_none(p.to_dict()), "Prospect() : aucun champ None")
    check(p.business_type == "autre", "business_type défaut = autre")
    check(p.website_quality == "unknown", "website_quality défaut = unknown")
    check(p.booking_quality == "unknown", "booking_quality défaut = unknown")
    check(p.activity_signal == "unknown", "activity_signal défaut = unknown")
    check(p.business_size == "unknown", "business_size défaut = unknown")
    check(p.estimated_ticket_size == "unknown", "ticket_size défaut = unknown")
    check(p.estimated_decision_complexity == "unknown", "decision_complexity défaut = unknown")
    check(p.has_website is False and p.has_booking is False, "has_* défaut = False")
    check(p.booking_software == "Aucun", "booking_software défaut = Aucun")
    check(p.booking_details == "", "booking_details défaut = '' (prose d'affichage)")
    check(p.ville == "", "ville défaut = ''")
    check(p.besoins == [] and p.raw_google_types == [], "listes défaut = []")
    check(p.cta_presence == "unknown", "cta_presence défaut = unknown")
    check(p.website_freshness == "unknown", "website_freshness défaut = unknown")
    check(p.friction_score == 0, "friction_score défaut = 0")
    check(p.friction_flags == [], "friction_flags défaut = []")
    check(p.bps == 0, "bps défaut = 0")
    check(p.bps_breakdown == {}, "bps_breakdown défaut = {}")


# --- Dérivations ------------------------------------------------------------

def test_derivations():
    print("test_derivations")
    check(derive_business_type(["gym", "point_of_interest"]) == "coach_sportif", "type: gym -> coach_sportif")
    check(derive_business_type(["beauty_salon"]) == "beaute", "type: beauty_salon -> beaute")
    check(derive_business_type(["inconnu"]) == "autre", "type: inconnu -> autre")
    check(derive_business_type([]) == "autre", "type: vide -> autre")
    check(derive_business_type(None) == "autre", "type: None -> autre")

    check(derive_activity_signal(None, None) == "unknown", "signal: None -> unknown")
    check(derive_activity_signal(5, 4.0) == "low", "signal: 5 avis -> low")
    check(derive_activity_signal(50, 4.0) == "medium", "signal: 50 avis -> medium")
    check(derive_activity_signal(200, 4.5) == "high", "signal: 200 avis -> high")
    check(derive_activity_signal(200, 2.5) == "medium", "signal: 200 avis + note 2.5 -> medium")

    check(derive_business_size(None, None) == "unknown", "size: None -> unknown")
    check(derive_business_size(["beauty_salon"], 10) == "solo", "size: 10 avis -> solo")
    check(derive_business_size(["beauty_salon"], 100) == "small", "size: 100 avis -> small")
    check(derive_business_size(["beauty_salon"], 300) == "medium", "size: salon 300 avis -> medium")
    check(derive_business_size(["gym"], 300) == "small", "size: coach 300 avis -> small (plafonné)")
    check(derive_business_size(["beauty_salon"], 1000) == "large", "size: 1000 avis -> large")

    check(derive_ticket_size("coach_business") == "high", "ticket: coach_business -> high")
    check(derive_ticket_size("beaute") == "low", "ticket: beaute -> low")
    check(derive_ticket_size("autre") == "unknown", "ticket: autre -> unknown")
    check(derive_ticket_size("zzz") == "unknown", "ticket: clé inconnue -> unknown")

    check(derive_decision_complexity("solo") == "low", "complexity: solo -> low")
    check(derive_decision_complexity("medium") == "medium", "complexity: medium -> medium")
    check(derive_decision_complexity("large") == "high", "complexity: large -> high")
    check(derive_decision_complexity("unknown") == "unknown", "complexity: unknown -> unknown")


# --- Sanitization / fallback unknown ----------------------------------------

def test_sanitize():
    print("test_sanitize")
    check(sanitize_choice("good", ("none", "good"), "unknown") == "good", "valeur valide conservée")
    check(sanitize_choice("excellent", ("none", "good"), "unknown") == "unknown", "valeur invalide -> unknown")
    check(sanitize_choice(None, ("none", "good"), "unknown") == "unknown", "None -> unknown")
    check(sanitize_choice(42, ("none", "good"), "unknown") == "unknown", "non-str -> unknown")


# --- Validation : normalise, jamais ne rejette ------------------------------

def test_validation_normalise():
    print("test_validation_normalise")
    p = Prospect()
    p.nom = None
    p.ville = None
    p.booking_software = None
    p.booking_details = None
    p.besoins = None
    p.raw_google_types = None
    p.raw_rating = None
    p.raw_user_ratings_total = None
    p.business_type = "zzz"
    p.website_quality = "excellent"
    p.activity_signal = "enorme"

    issues = []
    raised = False
    try:
        issues = validate_prospect(p)
    except Exception:
        raised = True

    check(not raised, "validate_prospect ne lève jamais")
    check(len(issues) > 0, "validate_prospect retourne les corrections")
    check(p.nom == "", "nom None -> ''")
    check(p.ville == "", "ville None -> ''")
    check(p.booking_software == "Aucun", "booking_software None -> Aucun")
    check(p.booking_details == "", "booking_details None -> ''")
    check(p.besoins == [], "besoins None -> []")
    check(p.raw_google_types == [], "raw_google_types None -> []")
    check(p.raw_rating == 0.0, "raw_rating None -> 0.0")
    check(p.raw_user_ratings_total == 0, "raw_user_ratings_total None -> 0")
    check(p.business_type == "autre", "business_type invalide -> autre")
    check(p.website_quality == "unknown", "website_quality invalide -> unknown")
    check(p.activity_signal == "unknown", "activity_signal invalide -> unknown")
    check(no_none(p.to_dict()), "après validation : aucun champ None")

    # Un prospect déjà valide ne génère aucune correction.
    check(validate_prospect(Prospect()) == [], "prospect valide : 0 correction")


# --- build_prospect : absence de None + fallback + aucun rejet --------------

def test_build_minimal():
    print("test_build_minimal")
    scraped = {"email": None, "facebook": None, "instagram": None, "text": ""}
    analysis = {}  # aucune clé : tout doit retomber sur les défauts
    google = {}
    p = build_prospect(nom="", telephone="", site_web="", scraped=scraped, analysis=analysis, google=google)
    check(isinstance(p, Prospect), "build_prospect retourne toujours un Prospect")
    check(no_none(p.to_dict()), "build minimal : aucun champ None")
    check(p.has_website is False, "build minimal : has_website False")
    check(p.website_quality == "unknown", "build minimal : website_quality unknown (clé absente)")
    check(p.booking_quality == "unknown", "build minimal : booking_quality unknown (clé absente)")
    check(p.activity_signal == "unknown", "build minimal : activity_signal unknown")
    check(p.business_type == "autre", "build minimal : business_type autre")
    check(p.raw_rating == 0.0 and p.raw_user_ratings_total == 0, "build minimal : raw numériques à 0")
    check(p.cta_presence == "unknown", "build minimal : cta_presence unknown (clé absente)")
    check(p.website_freshness == "unknown", "build minimal : website_freshness unknown (clé absente)")
    check(p.friction_score == 0 and p.friction_flags == [], "build minimal : friction non calculée (0 / [])")


def test_build_rich():
    print("test_build_rich")
    scraped = {"email": "a@b.com", "facebook": "fb", "instagram": "ig", "text": "..."}
    analysis = {
        "activites": "coach sportif",
        "besoins": ["Pas de site"],
        "booking_software": "Planity",
        "notes": "ok",
        "website_quality": "good",
        "booking_quality": "advanced",
        "cta_presence": "present",
        "website_freshness": "fresh",
    }
    google = {"types": ["gym"], "rating": 4.6, "user_ratings_total": 240, "business_status": "OPERATIONAL"}
    p = build_prospect(nom="X", telephone="01", site_web="http://x", scraped=scraped, analysis=analysis, google=google)
    check(no_none(p.to_dict()), "build riche : aucun champ None")
    check(p.has_website is True, "build riche : has_website True")
    check(p.has_booking is True, "build riche : has_booking True (Planity)")
    check(p.business_type == "coach_sportif", "build riche : business_type coach_sportif")
    check(p.business_size == "small", "build riche : business_size small (coach plafonné)")
    check(p.activity_signal == "high", "build riche : activity_signal high")
    check(p.website_quality == "good", "build riche : website_quality good")
    check(p.booking_quality == "advanced", "build riche : booking_quality advanced")
    check(p.estimated_ticket_size == "medium", "build riche : ticket_size medium")
    check(p.estimated_decision_complexity == "low", "build riche : complexity low")
    check(p.raw_user_ratings_total == 240 and p.raw_rating == 4.6, "build riche : raw Google conservés")
    check(p.cta_presence == "present", "build riche : cta_presence present")
    check(p.website_freshness == "fresh", "build riche : website_freshness fresh")
    check(p.friction_score == 0 and p.friction_flags == [], "build riche : friction non calculée par build_prospect")


def test_build_invalid_quality_fallback():
    print("test_build_invalid_quality_fallback")
    scraped = {"email": None, "facebook": None, "instagram": None, "text": ""}
    analysis = {"website_quality": "excellent", "booking_quality": "magique", "booking_software": "Aucun"}
    google = {}
    p = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis=analysis, google=google)
    check(p.website_quality == "unknown", "qualité site invalide -> unknown")
    check(p.booking_quality == "unknown", "qualité booking invalide -> unknown")
    check(p.has_booking is False, "booking_software Aucun -> has_booking False")


def test_new_signal_fields():
    print("test_new_signal_fields")
    p = Prospect()
    check(p.franchise_signal == "unknown", "franchise_signal défaut = unknown")
    check(p.network_signal == "unknown", "network_signal défaut = unknown")
    check(p.decision_complexity_reason == "", "decision_complexity_reason défaut = ''")

    p.franchise_signal = "zzz"
    p.network_signal = None
    p.decision_complexity_reason = None
    issues = validate_prospect(p)
    check(p.franchise_signal == "unknown", "franchise_signal invalide -> unknown")
    check(p.network_signal == "unknown", "network_signal None -> unknown")
    check(p.decision_complexity_reason == "", "decision_complexity_reason None -> ''")
    check(len(issues) >= 1, "validation retourne les corrections")

    p2 = Prospect(franchise_signal="confirmed", network_signal="suspected")
    validate_prospect(p2)
    check(p2.franchise_signal == "confirmed", "franchise_signal valide conservé")
    check(p2.network_signal == "suspected", "network_signal valide conservé")
    check(no_none(p2.to_dict()), "Prospect avec signaux : aucun champ None")


def test_friction_fields():
    print("test_friction_fields")
    p = Prospect()
    p.cta_presence = "zzz"
    p.website_freshness = None
    p.friction_flags = None
    p.friction_score = None
    issues = validate_prospect(p)
    check(p.cta_presence == "unknown", "cta_presence invalide -> unknown")
    check(p.website_freshness == "unknown", "website_freshness None -> unknown")
    check(p.friction_flags == [], "friction_flags None -> []")
    check(p.friction_score == 0, "friction_score None -> 0")
    check(len(issues) >= 1, "validation retourne les corrections")

    p2 = Prospect(cta_presence="absent", website_freshness="outdated")
    validate_prospect(p2)
    check(p2.cta_presence == "absent", "cta_presence valide conservé")
    check(p2.website_freshness == "outdated", "website_freshness valide conservé")
    check(no_none(p2.to_dict()), "Prospect avec irritants : aucun champ None")


def test_bps_fields():
    print("test_bps_fields")
    p = Prospect()
    check(p.bps == 0, "bps défaut = 0")
    check(p.bps_breakdown == {}, "bps_breakdown défaut = {}")

    p.bps = None
    p.bps_breakdown = None
    issues = validate_prospect(p)
    check(p.bps == 0, "bps None -> 0")
    check(p.bps_breakdown == {}, "bps_breakdown None -> {}")
    check(len(issues) >= 1, "validation retourne les corrections")

    p2 = Prospect(bps=72, bps_breakdown={"digital_need": {"score": 1.0}})
    validate_prospect(p2)
    check(p2.bps == 72, "bps valide conservé")
    check(p2.bps_breakdown == {"digital_need": {"score": 1.0}}, "bps_breakdown valide conservé")
    check(no_none(p2.to_dict()), "Prospect scoré : aucun champ None")


# --- Repli mot-clé -> business_type (A5) ------------------------------------

def test_derive_business_type_from_keyword():
    print("test_derive_business_type_from_keyword")
    check(derive_business_type_from_keyword("coach sportif") == "coach_sportif", "coach sportif -> coach_sportif")
    check(derive_business_type_from_keyword("Coach Sportif Lyon") == "coach_sportif", "casse/contexte ignorés")
    check(derive_business_type_from_keyword("nutritionniste") == "coach_nutrition", "nutritionniste -> coach_nutrition")
    check(derive_business_type_from_keyword("diététicien") == "coach_nutrition", "diététicien -> coach_nutrition")
    check(derive_business_type_from_keyword("coach business") == "coach_business", "coach business -> coach_business")
    check(derive_business_type_from_keyword("photographe mariage") == "photographie", "photographe -> photographie")
    check(derive_business_type_from_keyword("sophrologue") == "therapie", "sophrologue -> therapie")
    check(derive_business_type_from_keyword("naturopathe") == "bien_etre", "naturopathe -> bien_etre")
    check(derive_business_type_from_keyword("institut de beauté") == "beaute", "institut de beauté -> beaute")
    check(derive_business_type_from_keyword("coach") == "coach_sportif", "coach générique -> coach_sportif (repli)")
    check(derive_business_type_from_keyword("plombier") == "autre", "hors ICP -> autre")
    check(derive_business_type_from_keyword("") == "autre", "vide -> autre")
    check(derive_business_type_from_keyword(None) == "autre", "None -> autre")
    # Spécifique avant générique : "coach business" ne tombe pas sur "coach".
    check(derive_business_type_from_keyword("coach business développement") == "coach_business",
          "spécifique prioritaire sur le générique 'coach'")


def test_build_keyword_fallback_business_type():
    print("test_build_keyword_fallback_business_type")
    scraped = {"email": None, "facebook": None, "instagram": None, "text": ""}
    google = {"types": ["point_of_interest", "establishment"]}  # aucun type métier
    p0 = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis={}, google=google)
    check(p0.business_type == "autre", "types non ICP + pas de keyword -> autre")
    p1 = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis={}, google=google, keyword="coach sportif")
    check(p1.business_type == "coach_sportif", "repli keyword 'coach sportif' -> coach_sportif")
    check(p1.estimated_ticket_size == "medium", "ticket suit le type repli (coach_sportif -> medium)")
    # Le type Google reconnu PRIME sur le keyword (jamais écrasé).
    g2 = {"types": ["beauty_salon"]}
    p2 = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis={}, google=g2, keyword="coach sportif")
    check(p2.business_type == "beaute", "type Google reconnu prime sur le keyword")


def test_build_sets_ville():
    print("test_build_sets_ville")
    scraped = {"email": None, "facebook": None, "instagram": None, "text": ""}
    p = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis={}, google={}, ville="Lyon")
    check(p.ville == "Lyon", "ville alimentée depuis le pipeline")
    p2 = build_prospect(nom="X", telephone="", site_web="", scraped=scraped, analysis={}, google={})
    check(p2.ville == "", "ville par défaut = '' (rétrocompatible)")


# --- Câblage B2/B4 : signaux factuels prioritaires sur la supposition LLM ----

def test_build_booking_from_scrape():
    print("test_build_booking_from_scrape")
    # Widget réellement détecté dans le HTML (scrape) vs supposition Claude :
    # le signal FACTUEL prime.
    scraped = {"email": None, "facebook": None, "instagram": None, "text": "",
               "booking_software": "Calendly"}
    analysis = {"booking_software": "Planity", "booking_quality": "unknown"}
    p = build_prospect(nom="X", telephone="", site_web="http://x", scraped=scraped, analysis=analysis, google={})
    check(p.booking_software == "Calendly", "booking détecté au scrape prime sur l'analyse Claude")
    check(p.has_booking is True, "widget détecté -> has_booking True")
    check(p.booking_quality == "basic", "widget détecté + Claude muet -> booking_quality rehaussé à basic")
    # Sans détection au scrape, repli sur l'analyse Claude (rétrocompatible).
    scraped2 = {"email": None, "facebook": None, "instagram": None, "text": ""}
    p2 = build_prospect(nom="X", telephone="", site_web="http://x", scraped=scraped2, analysis={"booking_software": "Planity"}, google={})
    check(p2.booking_software == "Planity", "aucune détection scrape -> repli sur l'analyse Claude")

    # booking_details (prose d'affichage) NE PILOTE PAS le signal : token "Aucun"
    # + prose riche -> has_booking reste False (donnée d'affichage ≠ signal).
    scraped3 = {"email": None, "facebook": None, "instagram": None, "text": ""}
    analysis3 = {"booking_software": "Aucun",
                 "booking_details": "Pas d'outil dédié : réservation via un lien Facebook uniquement."}
    p3 = build_prospect(nom="X", telephone="", site_web="http://x", scraped=scraped3, analysis=analysis3, google={})
    check(p3.booking_software == "Aucun", "token canonique préservé (Aucun)")
    check(p3.has_booking is False, "prose booking_details n'active JAMAIS has_booking")
    check(p3.booking_details == "Pas d'outil dédié : réservation via un lien Facebook uniquement.",
          "booking_details (prose) transmis tel quel pour l'affichage Notion")


def test_build_franchise_from_name():
    print("test_build_franchise_from_name")
    scraped = {"email": None, "facebook": None, "instagram": None, "text": ""}
    # Marque connue dans le NOM Google -> franchise/réseau CONFIRMÉS (signal fort).
    p = build_prospect(nom="Basic Fit Lyon Part-Dieu", telephone="", site_web="", scraped=scraped, analysis={}, google={})
    check(p.franchise_signal == "confirmed", "marque connue dans le nom -> franchise_signal confirmed")
    check(p.network_signal == "confirmed", "marque connue dans le nom -> network_signal confirmed")
    # Nom neutre + texte absent -> aucune supposition (unknown, jamais 'no').
    p2 = build_prospect(nom="Studio Pilates Indé", telephone="", site_web="", scraped=scraped, analysis={}, google={})
    check(p2.franchise_signal == "unknown", "nom neutre + texte absent -> franchise unknown (donnée absente ≠ propre)")


def test_build_website_accessibility():
    print("test_build_website_accessibility")
    # Verdict de la sonde (scraper.check_website_accessibility) transitant par
    # `scraped` -> posé en ATTRIBUT D'INSTANCE (hors dataclass).
    scraped = {"email": None, "facebook": None, "instagram": None, "text": "",
               "website_unreachable": True, "website_unreachable_reason": "404"}
    p = build_prospect(nom="X", telephone="", site_web="http://x", scraped=scraped, analysis={}, google={})
    check(p.website_unreachable is True, "verdict 'inaccessible' mappé sur le prospect")
    check(p.website_unreachable_reason == "404", "raison interne transmise (404)")
    # Zéro impact Notion/scoring : le flag reste HORS de to_dict() (instance ≠ champ).
    check("website_unreachable" not in p.to_dict(), "flag hors to_dict() -> aucun impact structure Notion")
    check(no_none(p.to_dict()), "invariant préservé : aucun champ None dans to_dict()")
    # Clés absentes (cas neutre / scrape monkeypatché) -> None, sans rien casser.
    neutre = {"email": None, "facebook": None, "instagram": None, "text": ""}
    p2 = build_prospect(nom="X", telephone="", site_web="", scraped=neutre, analysis={}, google={})
    check(p2.website_unreachable is None, "clé absente -> None (incertain, jamais forcé)")
    check(p2.website_unreachable_reason == "", "raison absente -> '' (jamais None)")


def main():
    for test in (
        test_defaults,
        test_derivations,
        test_sanitize,
        test_validation_normalise,
        test_build_minimal,
        test_build_rich,
        test_build_invalid_quality_fallback,
        test_new_signal_fields,
        test_friction_fields,
        test_bps_fields,
        test_derive_business_type_from_keyword,
        test_build_keyword_fallback_business_type,
        test_build_sets_ville,
        test_build_booking_from_scrape,
        test_build_franchise_from_name,
        test_build_website_accessibility,
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
