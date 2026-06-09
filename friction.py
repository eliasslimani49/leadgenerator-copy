"""Détection des irritants digitaux et Friction Score (pur, déterministe).

Mesure le besoin digital réel d'un prospect déjà éligible (post-filtres).
Le Friction Score est une somme pondérée d'irritants détectés : explicable,
traçable, configurable via FRICTION_WEIGHTS. Aucun I/O, aucun appel Claude.

Convention « donnée absente » : un signal inconnu/absent ne déclenche jamais
un irritant. Les irritants liés au site (website_outdated, no_cta, poor_ux)
exigent has_website=True ET une valeur positive du signal correspondant.
"""

from typing import List

from model import Prospect

# Irritant -> poids dans le Friction Score (V2, configurable).
# phone_only a été RETIRÉ : il se déclenchait dès qu'il manquait une réservation
# en ligne, faisant double emploi avec no_booking (même irritant compté deux
# fois). Plafond résultant symétrique = 5 (cf. bps.FRICTION_SCORE_MAX).
FRICTION_WEIGHTS = {
    "no_website": 3,
    "no_booking": 2,
    "website_outdated": 1,
    "no_cta": 1,
    "poor_ux": 1,
}

# Irritant FACTUEL -> libellé « Besoin / Problème » Notion (valeurs exactes du
# multi-select). Seuls les irritants qui correspondent à un besoin client lisible
# sont mappés ; les autres restent des signaux internes de scoring. Permet
# d'alimenter Notion avec des besoins OBSERVÉS, pas seulement supposés par Claude.
FLAG_TO_BESOIN = {
    "no_website": "Pas de site",
    "no_booking": "Pas de réservation en ligne",
}


# --- Détecteurs (purs, booléens) --------------------------------------------


def no_website(prospect: Prospect) -> bool:
    return not prospect.has_website


def no_booking(prospect: Prospect) -> bool:
    return not prospect.has_booking


def website_outdated(prospect: Prospect) -> bool:
    return prospect.has_website and prospect.website_freshness == "outdated"


def no_cta(prospect: Prospect) -> bool:
    return prospect.has_website and prospect.cta_presence == "absent"


def poor_ux(prospect: Prospect) -> bool:
    return prospect.has_website and prospect.website_quality == "poor"


# Ordre stable des irritants (codes du Friction Score).
_DETECTORS = (
    ("no_website", no_website),
    ("no_booking", no_booking),
    ("website_outdated", website_outdated),
    ("no_cta", no_cta),
    ("poor_ux", poor_ux),
)


# --- Agrégation --------------------------------------------------------------


def friction_flags(prospect: Prospect) -> List[str]:
    """Codes des irritants détectés, dans un ordre stable."""
    return [code for code, detect in _DETECTORS if detect(prospect)]


def friction_score(prospect: Prospect) -> int:
    """Somme pondérée des irritants détectés."""
    return sum(FRICTION_WEIGHTS[code] for code in friction_flags(prospect))


def besoins_from_flags(flags) -> List[str]:
    """Besoins « Notion » FACTUELS déduits des irritants détectés (ordre stable,
    dédupliqués). Seuls les flags présents dans FLAG_TO_BESOIN sont traduits ;
    les autres restent des signaux internes. Tolère None -> []."""
    besoins: List[str] = []
    for flag in (flags or []):
        besoin = FLAG_TO_BESOIN.get(flag)
        if besoin and besoin not in besoins:
            besoins.append(besoin)
    return besoins


def assign_friction(prospect: Prospect) -> Prospect:
    """Calcule et écrit friction_flags + friction_score sur le prospect."""
    prospect.friction_flags = friction_flags(prospect)
    prospect.friction_score = friction_score(prospect)
    return prospect
