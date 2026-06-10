"""Moteur de filtres éliminatoires Vitryne.

Élimine les prospects hors cible AVANT tout scoring et avant l'appel Claude.
Fonctions pures, déterministes, traçables : aucune I/O, aucun appel réseau.
Le rejet ne se fonde jamais sur business_type ni sur la catégorie "autre".
"""

from typing import List

from model import Prospect

MIN_REVIEWS = 20

# Types Google Places hors modèle économique cible (vente de produits,
# restauration, retail, automobile, hôtellerie, finance...). Configurable.
# NE JAMAIS y inclure les types de service ci-dessous : ils restent éligibles.
#   spa, beauty_salon, hair_care, nail_salon, gym,
#   physiotherapist, doctor, dentist, health
INCOMPATIBLE_GOOGLE_TYPES = {
    "restaurant", "cafe", "bar", "bakery", "meal_takeaway", "meal_delivery", "food",
    "grocery_or_supermarket", "supermarket", "convenience_store", "liquor_store",
    "store", "clothing_store", "shoe_store", "jewelry_store", "furniture_store",
    "home_goods_store", "electronics_store", "hardware_store", "book_store",
    "pet_store", "department_store", "shopping_mall",
    "car_dealer", "car_rental", "car_repair", "car_wash", "gas_station",
    "lodging", "real_estate_agency", "bank", "atm", "pharmacy", "drugstore",
}


def is_incompatible_business(prospect: Prospect) -> bool:
    """Vrai si un type Google hors modèle économique est présent.

    Ne se fonde jamais sur business_type ni sur "autre" : un prospect sans type
    incompatible (y compris raw_google_types vide) reste éligible.
    """
    return any(t in INCOMPATIBLE_GOOGLE_TYPES for t in prospect.raw_google_types)


def has_no_contact(prospect: Prospect) -> bool:
    """Vrai si aucun canal de contact n'est disponible.

    Canaux considérés : téléphone, email, Instagram, Facebook. Le site web seul
    n'est pas un canal de contact direct exploitable.
    """
    return not (
        prospect.telephone
        or prospect.email
        or prospect.instagram
        or prospect.facebook
    )


def has_insufficient_reviews(prospect: Prospect) -> bool:
    """Vrai si le nombre d'avis est connu et strictement inférieur à MIN_REVIEWS.

    Un nombre d'avis inconnu (Google n'a rien fourni) n'entraîne pas de rejet.
    Au lot 1, "inconnu" est encodé par activity_signal == "unknown" (raw_user_
    ratings_total retombe alors sur 0, jamais None).
    """
    if prospect.activity_signal == "unknown":
        return False
    n = prospect.raw_user_ratings_total
    return n is not None and n < MIN_REVIEWS


def is_franchise_confirmed(prospect: Prospect) -> bool:
    """Vrai uniquement si une franchise est CONFIRMÉE (marque dans le nom Google).

    Seul "confirmed" rejette. "suspected" (marque/vocabulaire repérés dans le
    texte du site) n'élimine pas : il pénalise seulement la simplicité de
    décision via le BPS. Détection assurée par franchise.detect_franchise_signals.
    """
    return prospect.franchise_signal == "confirmed"


def is_network_confirmed(prospect: Prospect) -> bool:
    """Vrai uniquement si l'appartenance à un RÉSEAU multi-sites est confirmée.

    Même logique que la franchise : seul "confirmed" rejette (décision centralisée,
    pas de vente locale possible). "suspected" pénalise seulement le BPS.
    """
    return prospect.network_signal == "confirmed"


_RULES = (
    ("incompatible_business", is_incompatible_business),
    ("no_contact_channel", has_no_contact),
    ("reviews_below_min", has_insufficient_reviews),
    ("franchise_confirmed", is_franchise_confirmed),
    ("network_confirmed", is_network_confirmed),
)


def elimination_reasons(prospect: Prospect) -> List[str]:
    """Codes de rejet applicables, dans l'ordre des règles (vide = éligible)."""
    return [code for code, rule in _RULES if rule(prospect)]


def is_eliminated(prospect: Prospect) -> bool:
    return bool(elimination_reasons(prospect))
