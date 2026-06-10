"""Buy Probability Score (BPS) — score principal, pur et déterministe.

Agrège 6 dimensions en un score [0, 100] mesurant la probabilité d'achat de
Vitryne. Explicable (breakdown + journal d'ajustements), traçable, configurable
(BPS_WEIGHTS, plafonds, règles), sans algorithme opaque. Aucun I/O.

Méthode V3 (chaîne déterministe, cf. calculate_bps) :
  base pondérée (dims connues) -> facteur de CONFIANCE (couverture des signaux)
  -> bonus de synergie -> pénalités sur signaux NÉGATIFS connus -> plafond dur
  d'exclusion. Plus sélectif que la simple moyenne : seuls les meilleurs profils
  atteignent le seuil d'export (cf. segmentation.EXPORT_MIN_BPS).

Gestion de l'inconnu : un sous-score vaut None quand ses signaux sont inconnus ;
la dimension est alors EXCLUE (score None, jamais 0) et son poids redistribué.
Le facteur de confiance n'attribue PAS de valeur par défaut à une dimension
absente : il discounte seulement, de façon LÉGÈRE et bornée, la certitude globale
d'un score adossé à peu de signaux (désactivable via CONFIDENCE_FLOOR=1.0).
"""

from typing import NamedTuple, Optional

from targetly.core.model import Prospect

# Pondération des dimensions (V3, configurable, somme = 100).
# Concentre le poids sur les deux signaux d'achat les plus prédictifs — la
# douleur OBSERVÉE (current_friction) et le besoin LATENT du métier (digital_need),
# soit 52 % — puis sur la capacité/facilité à conclure (financial + decision = 34 %).
# La maturité (signal de sérieux secondaire) et l'accessibilité (quasi saturée et
# déjà filtrée en amont) sont allégées. "current_friction" = contribution de
# l'irritant mesuré (lot 3), à ne pas confondre avec le champ Prospect.friction_score.
BPS_WEIGHTS = {
    "current_friction": 30,
    "digital_need": 22,
    "financial_capacity": 18,
    "decision_simplicity": 16,
    "business_maturity": 9,
    "accessibility": 5,
}

# Plafond pratique du friction_score (lot 3) pour la normalisation [0, 1].
# Aligné sur le max RÉEL de friction.py après retrait de l'irritant redondant
# phone_only : no_website(3)+no_booking(2)=5 hors-ligne ; no_booking(2)+
# website_outdated(1)+no_cta(1)+poor_ux(1)=5 avec site -> plafond symétrique 5.
FRICTION_SCORE_MAX = 5

# --- Ajustements V3 : confiance, bonus, pénalité, exclusion (configurables) --

# Facteur de confiance = CONFIDENCE_FLOOR + (1 - CONFIDENCE_FLOOR) × couverture,
# où couverture = poids des dimensions CONNUES / poids total. Discount LÉGER et
# borné d'un score adossé à peu de signaux (un prospect mal connu ne peut pas
# truster le haut du classement). Ne met JAMAIS une dimension absente à 0 : c'est
# une confiance sur l'AGRÉGAT, pas une valeur par défaut. 1.0 = discount désactivé.
CONFIDENCE_FLOOR = 0.80

# Bonus additifs : récompensent les SYNERGIES d'achat que la somme linéaire
# sous-pondère (le tout vaut plus que la somme des parties). Plafond global.
BONUS_CAP = 10

# Pénalités soustractives : sanctionnent des signaux NÉGATIFS effectivement
# CONNUS (jamais une donnée absente). Plafond global.
PENALTY_CAP = 15

# Plafond dur appliqué si une règle d'EXCLUSION se déclenche : le score ne peut
# alors PLUS atteindre le seuil d'export. On PLAFONNE (pas de mise à 0 silencieuse)
# -> le prospect reste scoré/traçable, mais jamais promu vers Notion.
EXCLUSION_CEIL = 45

# --- Tables de mapping (configurables) --------------------------------------

_TICKET_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.3}
# Capacité financière complétée par la TAILLE estimée (proxy de surface
# financière) : corrige le biais du seul panier moyen — un métier à panier bas
# mais structuré (ex. salon de beauté à fort volume) n'est plus sous-évalué.
# Combinée en MOYENNE au panier (cf. score_financial_capacity). "unknown" absent
# -> composante exclue.
_SIZE_SCORE = {"solo": 0.3, "small": 0.6, "medium": 0.8, "large": 1.0}
_COMPLEXITY_SCORE = {"low": 1.0, "medium": 0.5, "high": 0.1}
_ACTIVITY_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.3}
_CHANNEL_WEIGHTS = {"telephone": 0.4, "email": 0.4, "instagram": 0.1, "facebook": 0.1}

# Multiplicateur de pénalité sur la simplicité de décision selon le signal
# franchise/réseau (le plus fort des deux axes s'applique). CONFIGURABLE par
# niveau. "confirmed" est normalement EXCLU en amont par les filtres
# (élimination VISIBLE) ; la valeur ici n'est qu'un garde-fou et reste > 0 :
# le score n'écarte JAMAIS un prospect silencieusement, il ne fait que l'abaisser.
_DECISION_SIGNAL_PENALTY = {
    "confirmed": 0.3,
    "suspected": 0.6,
    "no": 1.0,
    "unknown": 1.0,
}

# Besoin digital LATENT par métier : dépendance structurelle de l'activité à une
# présence/réservation en ligne (vitrine, prise de RDV). ORTHOGONAL à la friction
# réellement observée (lot 3) -> supprime le double comptage besoin/irritant.
# Signal FACTUEL (métier déduit de Google + mot-clé), pas une supposition LLM.
# "autre" = besoin latent FAIBLE mais CONNU (0.15) : un métier non ciblé ne doit
# plus voir sa dimension exclue puis son poids redistribué — c'est ce qui gonflait
# à tort le BPS des "autre" à forte friction (un irritant élevé suffisait à
# atteindre une bande haute). Configurable : une ligne = un métier.
_DIGITAL_NEED_BY_TYPE = {
    "beaute": 1.0,
    "bien_etre": 0.9,
    "therapie": 0.9,
    "coach_sportif": 0.9,
    "coach_nutrition": 0.8,
    "photographie": 0.7,
    "coach_business": 0.6,
    "autre": 0.15,
}


# --- Sous-scores (purs, [0, 1] ou None si inconnu) --------------------------


def score_digital_need(p: Prospect) -> Optional[float]:
    """Besoin digital LATENT, structurel au métier (orthogonal à la friction).

    Mesure à quel point ce TYPE d'activité dépend d'une présence/réservation en
    ligne, indépendamment de ce que le prospect possède déjà. L'écart réellement
    observé (site/résa absents, etc.) est porté SÉPARÉMENT par
    score_current_friction : plus aucun double comptage besoin/irritant.
    business_type "autre" -> 0.15 (besoin latent faible mais CONNU : on évite que
    la redistribution du poids d'une dimension exclue ne gonfle le BPS d'un métier
    non ciblé). Un type hors table -> None (garde-fou défensif ; jamais atteint
    pour un prospect validé, dont le business_type est toujours borné).
    """
    return _DIGITAL_NEED_BY_TYPE.get(p.business_type)


def score_current_friction(p: Prospect) -> Optional[float]:
    """Irritants mesurés (lot 3) : friction haute = besoin = contribution +."""
    return min(p.friction_score / FRICTION_SCORE_MAX, 1.0)


def score_financial_capacity(p: Prospect) -> Optional[float]:
    """Capacité financière = MOYENNE des composantes connues : panier moyen estimé
    (par métier) et TAILLE estimée (proxy de surface financière).

    Croiser les deux corrige le biais du seul panier : un métier à panier bas mais
    à structure étoffée (volume d'avis élevé) n'est plus systématiquement
    sous-évalué. Chaque composante "unknown" est EXCLUE ; si les deux sont
    inconnues -> None (dimension exclue du BPS, poids redistribué, jamais pénalisée).
    """
    comps = []
    ticket = _TICKET_SCORE.get(p.estimated_ticket_size)  # None si "unknown"
    if ticket is not None:
        comps.append(ticket)
    size = _SIZE_SCORE.get(p.business_size)               # None si "unknown"
    if size is not None:
        comps.append(size)
    return sum(comps) / len(comps) if comps else None


def score_decision_simplicity(p: Prospect) -> Optional[float]:
    """Simplicité de décision, pénalisée si la décision n'est pas locale.

    Un signal franchise/réseau (décision centralisée) réduit la simplicité via un
    multiplicateur CONFIGURABLE (_DECISION_SIGNAL_PENALTY) : on retient le plus
    fort des deux axes (le plus petit multiplicateur). La pénalité ne vaut JAMAIS
    0 -> un soupçon abaisse le score sans exclure ; l'exclusion des cas "confirmed"
    reste le rôle EXPLICITE des filtres (jamais une exclusion silencieuse ici).
    """
    base = _COMPLEXITY_SCORE.get(p.estimated_decision_complexity)  # None si "unknown"
    if base is None:
        return None
    penalty = min(
        _DECISION_SIGNAL_PENALTY.get(p.franchise_signal, 1.0),
        _DECISION_SIGNAL_PENALTY.get(p.network_signal, 1.0),
    )
    return base * penalty


def score_business_maturity(p: Prospect) -> Optional[float]:
    """Maturité de l'entreprise (activité réelle + réputation), pas digitale."""
    comps = []
    activity = _ACTIVITY_SCORE.get(p.activity_signal)  # None si "unknown"
    if activity is not None:
        comps.append(activity)
    if p.raw_user_ratings_total > 0:  # note significative seulement si avis connus
        r = p.raw_rating
        if r >= 4.5:
            comps.append(1.0)
        elif r >= 4.0:
            comps.append(0.8)
        elif r >= 3.0:
            comps.append(0.5)
        else:
            comps.append(0.2)
    if not comps:
        return None
    return sum(comps) / len(comps)


def score_accessibility(p: Prospect) -> Optional[float]:
    """Facilité de contact pour l'outbound (jamais inconnue : canaux ou rien)."""
    total = 0.0
    if p.telephone:
        total += _CHANNEL_WEIGHTS["telephone"]
    if p.email:
        total += _CHANNEL_WEIGHTS["email"]
    if p.instagram:
        total += _CHANNEL_WEIGHTS["instagram"]
    if p.facebook:
        total += _CHANNEL_WEIGHTS["facebook"]
    return min(total, 1.0)


# Ordre stable des dimensions du BPS.
_SCORERS = (
    ("digital_need", score_digital_need),
    ("current_friction", score_current_friction),
    ("financial_capacity", score_financial_capacity),
    ("decision_simplicity", score_decision_simplicity),
    ("business_maturity", score_business_maturity),
    ("accessibility", score_accessibility),
)


# --- Règles d'ajustement (bonus / pénalité / exclusion), V3 ------------------
# Chaque règle est un prédicat PUR (subs, prospect) -> bool ; subs = sous-scores
# bruts {dim: [0, 1] | None}. Tout est explicite, traçable et configurable
# (codes + points). Aucune règle ne se fonde sur une donnée ABSENTE (None).


def _bonus_metier_fit_with_pain(subs, p) -> bool:
    """Bullseye Vitryne : métier à fort besoin latent ET irritant fort observé."""
    dn, cf = subs.get("digital_need"), subs.get("current_friction")
    return dn is not None and cf is not None and dn >= 0.9 and cf >= 0.8


def _bonus_easy_to_close(subs, p) -> bool:
    """Vente rapide : décision locale/simple ET capacité financière correcte."""
    ds, fc = subs.get("decision_simplicity"), subs.get("financial_capacity")
    return ds is not None and ds >= 0.9 and fc is not None and fc >= 0.6


def _bonus_clear_fixable_gap(subs, p) -> bool:
    """Manque CONCRET et adressable (ni site ni résa) sur un métier à fort besoin."""
    dn = subs.get("digital_need")
    flags = set(p.friction_flags or [])
    return dn is not None and dn >= 0.8 and {"no_website", "no_booking"} <= flags


# code -> prédicat -> points. Somme plafonnée à BONUS_CAP.
_BONUS_RULES = (
    ("metier_fit_with_pain", _bonus_metier_fit_with_pain, 6),
    ("easy_to_close", _bonus_easy_to_close, 4),
    ("clear_fixable_gap", _bonus_clear_fixable_gap, 3),
)


def _penalty_no_direct_contact(subs, p) -> bool:
    """Ni téléphone ni email : outbound réel difficile (réseaux sociaux seuls)."""
    return not (p.telephone or p.email)


def _penalty_weak_reputation(subs, p) -> bool:
    """Réputation faible CONNUE (avis présents, note < 3.5) : vente plus dure."""
    return p.raw_user_ratings_total > 0 and p.raw_rating < 3.5


# code -> prédicat -> points retranchés. Somme plafonnée à PENALTY_CAP.
_PENALTY_RULES = (
    ("no_direct_contact", _penalty_no_direct_contact, 8),
    ("weak_reputation", _penalty_weak_reputation, 5),
)


def _exclude_no_pain_to_solve(subs, p) -> bool:
    """Aucun irritant digital (friction nulle) : rien à corriger -> pas acheteur."""
    return p.friction_score == 0


def _exclude_inactive_business(subs, p) -> bool:
    """Établissement non opérationnel (fermé/suspendu) CONNU -> invendable.

    Statut inconnu (vide) ne déclenche jamais : donnée absente ≠ exclusion.
    """
    status = (p.raw_business_status or "").strip().upper()
    return bool(status) and status != "OPERATIONAL"


# Déclenchent le plafond dur EXCLUSION_CEIL (jamais une mise à 0 silencieuse).
_EXCLUSION_RULES = (
    ("no_pain_to_solve", _exclude_no_pain_to_solve),
    ("inactive_business", _exclude_inactive_business),
)


# --- Agrégation --------------------------------------------------------------


class BpsResult(NamedTuple):
    bps: int
    breakdown: dict
    adjustments: dict = {}  # journal traçable de la chaîne V3 (base, confiance, ...)


def calculate_bps(prospect: Prospect) -> BpsResult:
    """Score [0, 100] = base pondérée -> confiance -> bonus/pénalité -> exclusion.

    Chaîne V3, déterministe et entièrement traçable (cf. champ adjustments) :
      1. base       : moyenne pondérée sur les dimensions CONNUES (re-normalisée) ;
      2. confiance  : discount léger et borné selon la couverture des signaux ;
      3. bonus      : synergies d'achat (plafonné BONUS_CAP) ;
      4. pénalités  : signaux négatifs CONNUS (plafonné PENALTY_CAP) ;
      5. exclusion  : plafond dur EXCLUSION_CEIL si un disqualifiant se déclenche.
    Une dimension inconnue reste exclue (score None, jamais 0) dans le breakdown.
    """
    raw = {name: scorer(prospect) for name, scorer in _SCORERS}
    total_weight = sum(BPS_WEIGHTS[name] for name, value in raw.items() if value is not None)

    breakdown = {}
    weighted_sum = 0.0
    for name, value in raw.items():
        weight = BPS_WEIGHTS[name]
        if value is None:
            breakdown[name] = {"score": None, "weight": weight, "known": False, "contribution": 0.0}
            continue
        weighted_sum += weight * value
        contribution = (100 * weight * value / total_weight) if total_weight else 0.0
        breakdown[name] = {
            "score": round(value, 3),
            "weight": weight,
            "known": True,
            "contribution": round(contribution, 1),
        }

    # 1. Base : moyenne pondérée re-normalisée sur les seules dimensions connues.
    base = (100 * weighted_sum / total_weight) if total_weight else 0.0

    # 2. Confiance : couverture = poids connu / poids total (somme des poids = 100).
    coverage = total_weight / sum(BPS_WEIGHTS.values())
    confidence = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * coverage
    adjusted = base * confidence

    # 3-4. Bonus de synergie et pénalités sur signaux connus, chacun plafonné.
    fired_bonus = [(code, pts) for code, pred, pts in _BONUS_RULES if pred(raw, prospect)]
    fired_penalty = [(code, pts) for code, pred, pts in _PENALTY_RULES if pred(raw, prospect)]
    bonus = min(sum(pts for _, pts in fired_bonus), BONUS_CAP)
    penalty = min(sum(pts for _, pts in fired_penalty), PENALTY_CAP)
    score = adjusted + bonus - penalty

    # 5. Exclusion : plafond dur (jamais exporté), sans mise à 0 silencieuse.
    exclusions = [code for code, pred in _EXCLUSION_RULES if pred(raw, prospect)]
    if exclusions:
        score = min(score, EXCLUSION_CEIL)

    bps = int(max(0, min(100, round(score))))
    adjustments = {
        "base": round(base, 1),
        "coverage": round(coverage, 3),
        "confidence": round(confidence, 3),
        "adjusted": round(adjusted, 1),
        "bonus": bonus,
        "bonuses": [code for code, _ in fired_bonus],
        "penalty": penalty,
        "penalties": [code for code, _ in fired_penalty],
        "exclusions": exclusions,
        "final": bps,
    }
    return BpsResult(bps, breakdown, adjustments)


def assign_bps(prospect: Prospect) -> Prospect:
    """Calcule et écrit prospect.bps + prospect.bps_breakdown."""
    result = calculate_bps(prospect)
    prospect.bps = result.bps
    prospect.bps_breakdown = result.breakdown
    return prospect
