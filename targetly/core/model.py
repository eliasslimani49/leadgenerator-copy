"""Modèle de données prospect unique pour le scoring Targetly.

Socle déterministe et réutilisable : étape "Enrichissement" du pipeline,
en amont des filtres éliminatoires et du Buy Probability Score (BPS).
Ce module ne fait AUCUN scoring, filtrage, ranking ni appel réseau.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Literal

from targetly.core.franchise import detect_franchise_signals

# --- Types bornés -----------------------------------------------------------

BusinessType = Literal[
    "coach_sportif", "coach_nutrition", "coach_business",
    "bien_etre", "therapie", "photographie", "beaute", "autre",
]
WebsiteQuality = Literal["none", "poor", "average", "good", "unknown"]
BookingQuality = Literal["none", "basic", "advanced", "unknown"]
ActivitySignal = Literal["low", "medium", "high", "unknown"]
BusinessSize = Literal["solo", "small", "medium", "large", "unknown"]
Estimate = Literal["low", "medium", "high", "unknown"]
FranchiseSignal = Literal["unknown", "suspected", "confirmed", "no"]
NetworkSignal = Literal["unknown", "suspected", "confirmed", "no"]
CtaPresence = Literal["present", "absent", "unknown"]
WebsiteFreshness = Literal["fresh", "outdated", "unknown"]

# Valeurs autorisées pour la sanitization runtime (source unique de vérité).
BUSINESS_TYPES = (
    "coach_sportif", "coach_nutrition", "coach_business",
    "bien_etre", "therapie", "photographie", "beaute", "autre",
)
WEBSITE_QUALITIES = ("none", "poor", "average", "good", "unknown")
BOOKING_QUALITIES = ("none", "basic", "advanced", "unknown")
ACTIVITY_SIGNALS = ("low", "medium", "high", "unknown")
BUSINESS_SIZES = ("solo", "small", "medium", "large", "unknown")
ESTIMATES = ("low", "medium", "high", "unknown")
SIGNAL_VALUES = ("unknown", "suspected", "confirmed", "no")
CTA_PRESENCES = ("present", "absent", "unknown")
WEBSITE_FRESHNESS_VALUES = ("fresh", "outdated", "unknown")

# --- Tables configurables ----------------------------------------------------

# Token de type Google Places -> catégorie métier Targetly.
# Les coachs (nutrition / business) et le personal training n'ont pas de type
# Google standard : ils retombent sur "autre" (à affiner via le mot-clé de
# recherche ou l'analyse Claude dans un lot ultérieur).
BUSINESS_TYPE_MAP = {
    "gym": "coach_sportif",
    "physiotherapist": "therapie",
    "doctor": "therapie",
    "dentist": "therapie",
    "health": "therapie",
    "spa": "bien_etre",
    "beauty_salon": "beaute",
    "hair_care": "beaute",
    "nail_salon": "beaute",
    "photographer": "photographie",
}

# Repli mot-clé de recherche -> catégorie métier, quand les types Google
# retombent sur "autre" (cas des coachs/photographes sans type standard).
# Ordre = du plus spécifique au plus générique : la 1re sous-chaîne trouvée
# dans le mot-clé (en minuscules) gagne. "coach" générique en dernier.
KEYWORD_BUSINESS_TYPE_HINTS = (
    ("coach sportif", "coach_sportif"),
    ("coach business", "coach_business"),
    ("coach professionnel", "coach_business"),
    ("nutritionniste", "coach_nutrition"),
    ("diététicien", "coach_nutrition"),
    ("dieteticien", "coach_nutrition"),
    ("photographe", "photographie"),
    ("sophrologue", "therapie"),
    ("thérapeute", "therapie"),
    ("therapeute", "therapie"),
    ("psychologue", "therapie"),
    ("naturopathe", "bien_etre"),
    ("massage", "bien_etre"),
    ("spa", "bien_etre"),
    ("institut", "beaute"),
    ("beauté", "beaute"),
    ("beaute", "beaute"),
    ("coiffure", "beaute"),
    ("coiffeur", "beaute"),
    ("ongles", "beaute"),
    ("coach", "coach_sportif"),
)

# Catégorie métier -> panier moyen estimé d'une prestation.
TICKET_SIZE_MAP = {
    "coach_business": "high",
    "photographie": "high",
    "coach_sportif": "medium",
    "coach_nutrition": "medium",
    "bien_etre": "medium",
    "therapie": "medium",
    "beaute": "low",
    "autre": "unknown",
}

# Catégories par nature individuelles : on plafonne leur taille estimée.
_SOLO_BY_NATURE = {
    "coach_sportif", "coach_nutrition", "coach_business",
    "photographie", "therapie",
}

# --- Helpers -----------------------------------------------------------------


def sanitize_choice(value, allowed, default):
    """Retourne value si elle fait partie des valeurs bornées, sinon default."""
    if isinstance(value, str) and value in allowed:
        return value
    return default


# --- Fonctions de dérivation (pures) ----------------------------------------


def derive_business_type(types) -> str:
    """Première catégorie métier reconnue dans les types Google, sinon "autre"."""
    if not types:
        return "autre"
    for t in types:
        if t in BUSINESS_TYPE_MAP:
            return BUSINESS_TYPE_MAP[t]
    return "autre"


def derive_business_type_from_keyword(keyword) -> str:
    """Repli métier depuis le mot-clé de recherche (sous-chaîne, ordre spécifique
    -> générique). Retourne "autre" si aucun indice. N'écrase jamais un type
    Google déjà reconnu (l'appelant ne s'en sert que si le type vaut "autre").
    """
    if not keyword or not isinstance(keyword, str):
        return "autre"
    low = keyword.lower()
    for hint, business_type in KEYWORD_BUSINESS_TYPE_HINTS:
        if hint in low:
            return business_type
    return "autre"


def derive_activity_signal(user_ratings_total, rating) -> str:
    """Niveau d'activité estimé depuis le volume d'avis.

    Un volume élevé associé à une note très basse traduit une traction réelle
    mais fragile : on rétrograde alors de "high" à "medium".
    """
    if user_ratings_total is None:
        return "unknown"
    if user_ratings_total < 20:
        signal = "low"
    elif user_ratings_total <= 100:
        signal = "medium"
    else:
        signal = "high"
    if signal == "high" and rating is not None and rating < 3.0:
        signal = "medium"
    return signal


def derive_business_size(types, user_ratings_total) -> str:
    """Taille estimée depuis le volume d'avis, plafonnée pour les métiers solo."""
    if user_ratings_total is None:
        return "unknown"
    if user_ratings_total < 30:
        base = "solo"
    elif user_ratings_total < 150:
        base = "small"
    elif user_ratings_total < 500:
        base = "medium"
    else:
        base = "large"
    if derive_business_type(types) in _SOLO_BY_NATURE and base in ("medium", "large"):
        return "small"
    return base


def derive_ticket_size(business_type) -> str:
    return TICKET_SIZE_MAP.get(business_type, "unknown")


def derive_decision_complexity(business_size) -> str:
    return {
        "solo": "low",
        "small": "low",
        "medium": "medium",
        "large": "high",
        "unknown": "unknown",
    }.get(business_size, "unknown")


# --- Modèle ------------------------------------------------------------------


@dataclass
class Prospect:
    # Champs existants (rétrocompatibles avec le pipeline / Notion).
    nom: str = ""
    telephone: str = ""
    email: str = ""
    site_web: str = ""
    facebook: str = ""
    instagram: str = ""
    ville: str = ""
    activites: str = ""
    besoins: List[str] = field(default_factory=list)
    booking_software: str = "Aucun"
    # Prose factuelle d'affichage sur la réservation (cellule Notion « Booking
    # software »). NE PILOTE AUCUN signal : has_booking reste dérivé du seul
    # token booking_software ci-dessus.
    booking_details: str = ""
    notes: str = ""

    # Features dérivées (socle de scoring).
    business_size: BusinessSize = "unknown"
    business_type: BusinessType = "autre"
    has_website: bool = False
    website_quality: WebsiteQuality = "unknown"
    has_booking: bool = False
    booking_quality: BookingQuality = "unknown"
    activity_signal: ActivitySignal = "unknown"
    estimated_decision_complexity: Estimate = "unknown"
    estimated_ticket_size: Estimate = "unknown"

    # Signaux préparatoires (détection franchise/réseau dans un lot ultérieur).
    franchise_signal: FranchiseSignal = "unknown"
    network_signal: NetworkSignal = "unknown"
    decision_complexity_reason: str = ""

    # Irritants digitaux (signaux d'entrée renseignés par l'analyse Claude ;
    # friction_* est calculé par friction.py, hors de ce module).
    cta_presence: CtaPresence = "unknown"
    website_freshness: WebsiteFreshness = "unknown"
    friction_score: int = 0
    friction_flags: List[str] = field(default_factory=list)

    # Score principal (Buy Probability Score) — calculé par bps.py.
    bps: int = 0
    bps_breakdown: dict = field(default_factory=dict)

    # Données brutes Google (traçabilité, jamais None).
    raw_google_types: List[str] = field(default_factory=list)
    raw_rating: float = 0.0
    raw_user_ratings_total: int = 0
    raw_business_status: str = ""

    # Identifiant Google Place — clé unique de synchronisation Notion.
    place_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate_prospect(prospect: Prospect) -> List[str]:
    """Normalise le prospect en place et garantit l'absence de None.

    Ne lève jamais et ne rejette jamais un prospect : tout champ invalide ou
    None est remplacé par son défaut. Retourne la liste des corrections
    appliquées (vide si le prospect était déjà valide).
    """
    issues: List[str] = []

    text_defaults = {
        "nom": "", "telephone": "", "email": "", "site_web": "",
        "facebook": "", "instagram": "", "ville": "", "activites": "",
        "booking_software": "Aucun", "booking_details": "", "notes": "", "raw_business_status": "",
        "decision_complexity_reason": "", "place_id": "",
    }
    for name, default in text_defaults.items():
        if getattr(prospect, name) is None:
            setattr(prospect, name, default)
            issues.append(f"{name}: None -> {default!r}")

    if prospect.besoins is None:
        prospect.besoins = []
        issues.append("besoins: None -> []")
    if prospect.raw_google_types is None:
        prospect.raw_google_types = []
        issues.append("raw_google_types: None -> []")
    if prospect.friction_flags is None:
        prospect.friction_flags = []
        issues.append("friction_flags: None -> []")
    if prospect.bps_breakdown is None:
        prospect.bps_breakdown = {}
        issues.append("bps_breakdown: None -> {}")

    enum_checks = (
        ("business_type", BUSINESS_TYPES, "autre"),
        ("website_quality", WEBSITE_QUALITIES, "unknown"),
        ("booking_quality", BOOKING_QUALITIES, "unknown"),
        ("activity_signal", ACTIVITY_SIGNALS, "unknown"),
        ("business_size", BUSINESS_SIZES, "unknown"),
        ("estimated_ticket_size", ESTIMATES, "unknown"),
        ("estimated_decision_complexity", ESTIMATES, "unknown"),
        ("franchise_signal", SIGNAL_VALUES, "unknown"),
        ("network_signal", SIGNAL_VALUES, "unknown"),
        ("cta_presence", CTA_PRESENCES, "unknown"),
        ("website_freshness", WEBSITE_FRESHNESS_VALUES, "unknown"),
    )
    for name, allowed, default in enum_checks:
        if getattr(prospect, name) not in allowed:
            setattr(prospect, name, default)
            issues.append(f"{name}: invalide -> {default!r}")

    if prospect.raw_rating is None:
        prospect.raw_rating = 0.0
        issues.append("raw_rating: None -> 0.0")
    if prospect.raw_user_ratings_total is None:
        prospect.raw_user_ratings_total = 0
        issues.append("raw_user_ratings_total: None -> 0")
    if prospect.friction_score is None:
        prospect.friction_score = 0
        issues.append("friction_score: None -> 0")
    if prospect.bps is None:
        prospect.bps = 0
        issues.append("bps: None -> 0")

    prospect.has_website = bool(prospect.has_website)
    prospect.has_booking = bool(prospect.has_booking)

    return issues


def build_prospect(*, nom, telephone, site_web, scraped, analysis, google, place_id="", ville="", keyword="") -> Prospect:
    """Construit un Prospect normalisé à partir des sorties du pipeline.

    - nom / telephone / site_web : déjà résolus par le pipeline.
    - scraped : dict de scrape_website (email, facebook, instagram).
    - analysis : dict d'analyze_with_claude.
    - google : dict de get_place_details (types, rating, user_ratings_total...).
    - place_id : identifiant Google Place, clé unique de synchronisation Notion.
    - ville : ville de recherche (alimente prospect.ville pour le ranking/segmentation).
    - keyword : mot-clé de recherche, repli métier quand les types Google échouent.
    """
    types = google.get("types") or []
    rating = google.get("rating")
    n_avis = google.get("user_ratings_total")

    # Booking : le signal FACTUEL du scrape (widget réellement détecté dans le
    # HTML) prime sur la supposition Claude ; repli "Aucun" si rien.
    detected_booking = scraped.get("booking_software")
    booking_software = detected_booking or analysis.get("booking_software") or "Aucun"
    business_type = derive_business_type(types)
    if business_type == "autre":
        business_type = derive_business_type_from_keyword(keyword)
    business_size = derive_business_size(types, n_avis)

    # Qualité de réservation : si un widget a été détecté factuellement mais que
    # Claude n'a rien qualifié, on garantit au moins "basic" (mesuré > supposé).
    booking_quality = sanitize_choice(analysis.get("booking_quality"), BOOKING_QUALITIES, "unknown")
    if detected_booking and booking_quality in ("none", "unknown"):
        booking_quality = "basic"

    # Franchise / réseau : déduits FACTUELLEMENT du nom Google + texte du site.
    franchise_signal, network_signal = detect_franchise_signals(nom, scraped.get("text"))

    prospect = Prospect(
        nom=nom or "",
        telephone=telephone or "",
        email=scraped.get("email") or "",
        site_web=site_web or "",
        facebook=scraped.get("facebook") or "",
        instagram=scraped.get("instagram") or "",
        ville=ville or "",
        activites=analysis.get("activites") or "",
        besoins=list(analysis.get("besoins") or []),
        booking_software=booking_software,
        booking_details=analysis.get("booking_details") or "",
        notes=analysis.get("notes") or "",
        business_type=business_type,
        business_size=business_size,
        has_website=bool(site_web),
        website_quality=sanitize_choice(analysis.get("website_quality"), WEBSITE_QUALITIES, "unknown"),
        has_booking=booking_software.strip().lower() not in ("", "aucun"),
        booking_quality=booking_quality,
        cta_presence=sanitize_choice(analysis.get("cta_presence"), CTA_PRESENCES, "unknown"),
        website_freshness=sanitize_choice(analysis.get("website_freshness"), WEBSITE_FRESHNESS_VALUES, "unknown"),
        franchise_signal=franchise_signal,
        network_signal=network_signal,
        activity_signal=derive_activity_signal(n_avis, rating),
        estimated_ticket_size=derive_ticket_size(business_type),
        estimated_decision_complexity=derive_decision_complexity(business_size),
        raw_google_types=list(types),
        raw_rating=float(rating) if rating is not None else 0.0,
        raw_user_ratings_total=int(n_avis) if n_avis is not None else 0,
        raw_business_status=google.get("business_status") or "",
        place_id=place_id or "",
    )
    validate_prospect(prospect)
    # Accessibilité du site (sonde scraper.check_website_accessibility, transitant
    # par `scraped`). Attribut d'INSTANCE volontairement hors dataclass : invisible
    # à to_dict()/Notion (aucun impact structure) et au scoring ; lu uniquement par
    # le modèle Messenger via getattr. None = inconnu/incertain (jamais forcé).
    prospect.website_unreachable = scraped.get("website_unreachable")
    prospect.website_unreachable_reason = scraped.get("website_unreachable_reason") or ""
    return prospect
