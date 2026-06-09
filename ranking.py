"""Moteur de classement des prospects par Buy Probability Score (BPS).

Étape "Ranking" du pipeline, en aval du BPS. Module pur : aucun effet de
bord, aucun appel réseau, aucune écriture. Il ne modifie pas les prospects
et ne touche pas au flux (pipeline, SSE, Notion restent hors périmètre).

Produit uniquement : classement, élite, groupes, statistiques.
"""

from statistics import mean, median
from typing import Callable, Dict, List

from model import Prospect
from segmentation import EXPORT_MIN_BPS

# --- Configuration -----------------------------------------------------------

# Plancher d'éligibilité à l'élite : en dessous, le prospect n'est jamais
# exporté quel que soit son rang (garantit « aucun prospect faible exporté »).
# Aligné sur le seuil d'export CRM (segmentation), source unique de vérité :
# l'élite affichée ne peut donc jamais diverger des prospects réellement exportés.
MIN_BPS = EXPORT_MIN_BPS
TOP_STRICT = 5
TOP_EXTENDED = 10


# --- Classement (pur, déterministe) ------------------------------------------


def rank(prospects: List[Prospect]) -> List[Prospect]:
    """Trie par BPS décroissant ; départage par friction puis nom (stable)."""
    return sorted(prospects, key=lambda p: (-p.bps, -p.friction_score, p.nom))


def top_n(prospects: List[Prospect], n: int) -> List[Prospect]:
    """Les n meilleurs prospects par BPS, sans plancher d'éligibilité."""
    return rank(prospects)[:n]


def select_elite(prospects: List[Prospect], *, min_bps: int = MIN_BPS,
                 limit: int = TOP_EXTENDED) -> List[Prospect]:
    """Élite : prospects au-dessus du plancher BPS, classés et plafonnés à limit.

    Le plancher est appliqué AVANT la coupe : un Top N ne peut donc jamais
    faire remonter un prospect faible faute de candidats.
    """
    eligible = [p for p in prospects if p.bps >= min_bps]
    return rank(eligible)[:limit]


# --- Regroupements (chaque groupe est lui-même classé) -----------------------


def _group_by(prospects: List[Prospect], key_fn: Callable[[Prospect], str]) -> Dict[str, List[Prospect]]:
    groups: Dict[str, List[Prospect]] = {}
    for p in prospects:
        groups.setdefault(key_fn(p), []).append(p)
    return {key: rank(items) for key, items in groups.items()}


def group_by_business_type(prospects: List[Prospect]) -> Dict[str, List[Prospect]]:
    """{business_type: prospects classés}."""
    return _group_by(prospects, lambda p: p.business_type)


def group_by_city(prospects: List[Prospect]) -> Dict[str, List[Prospect]]:
    """{ville: prospects classés}. Les villes non renseignées tombent dans ""."""
    return _group_by(prospects, lambda p: p.ville)


def top_by_group(prospects: List[Prospect], key_fn: Callable[[Prospect], str],
                 n: int, *, min_bps: int = MIN_BPS) -> Dict[str, List[Prospect]]:
    """{groupe: élite du groupe (plancher BPS appliqué), plafonnée à n}."""
    result: Dict[str, List[Prospect]] = {}
    for key, items in _group_by(prospects, key_fn).items():
        result[key] = select_elite(items, min_bps=min_bps, limit=n)
    return result


# --- Statistiques (lecture seule) --------------------------------------------


def compute_stats(prospects: List[Prospect], *, min_bps: int = MIN_BPS) -> dict:
    """Synthèse chiffrée du lot classé. Tolère une liste vide (zéros / None)."""
    total = len(prospects)
    elite = [p for p in prospects if p.bps >= min_bps]
    scores = [p.bps for p in prospects]

    def _count_by(key_fn: Callable[[Prospect], str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for p in prospects:
            counts[key_fn(p)] = counts.get(key_fn(p), 0) + 1
        return counts

    return {
        "total": total,
        "min_bps": min_bps,
        "elite_count": len(elite),
        "bps_min": min(scores) if scores else None,
        "bps_max": max(scores) if scores else None,
        "bps_avg": round(mean(scores), 1) if scores else None,
        "bps_median": median(scores) if scores else None,
        "by_business_type": _count_by(lambda p: p.business_type),
        "by_city": _count_by(lambda p: p.ville),
    }
