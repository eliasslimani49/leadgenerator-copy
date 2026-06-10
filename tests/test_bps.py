import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests du Buy Probability Score (bps.py).

Runner autonome, sans dépendance externe :  python3 test_bps.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.core.model import Prospect
from targetly.core.bps import (
    BPS_WEIGHTS,
    FRICTION_SCORE_MAX,
    BONUS_CAP,
    PENALTY_CAP,
    EXCLUSION_CEIL,
    CONFIDENCE_FLOOR,
    _DECISION_SIGNAL_PENALTY,
    score_digital_need,
    score_current_friction,
    score_financial_capacity,
    score_decision_simplicity,
    score_business_maturity,
    score_accessibility,
    calculate_bps,
    assign_bps,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def approx(a, b, eps=1e-9):
    return a is not None and b is not None and abs(a - b) < eps


def ideal_prospect(**overrides):
    """Prospect « idéal » : chaque dimension vaut 1.0 -> BPS 100."""
    p = Prospect(
        telephone="01 23 45 67 89", email="a@b.com", instagram="ig", facebook="fb",
        business_type="beaute",  # digital_need latent = 1.0
        has_website=False, has_booking=False,
        estimated_ticket_size="high",
        estimated_decision_complexity="low",
        activity_signal="high",
        raw_user_ratings_total=300, raw_rating=4.8,
        friction_score=FRICTION_SCORE_MAX,
    )
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


# --- Sous-scores : bornes, inconnu, cas particuliers ------------------------


def test_subscores_known():
    print("test_subscores_known")
    check(approx(score_digital_need(Prospect(business_type="beaute")), 1.0), "digital_need: métier beaute -> 1.0 (besoin latent)")
    check(approx(score_digital_need(Prospect(business_type="coach_business")), 0.6), "digital_need: coach_business -> 0.6 (besoin latent)")
    check(approx(score_digital_need(Prospect(business_type="autre")), 0.15), "digital_need: autre -> 0.15 (faible mais connu, jamais exclu)")
    # financial = moyenne des composantes connues (panier moyen + taille estimée).
    check(approx(score_financial_capacity(Prospect(estimated_ticket_size="high")), 1.0), "financial: panier high seul (taille inconnue) -> 1.0")
    check(approx(score_financial_capacity(Prospect(estimated_ticket_size="low")), 0.3), "financial: panier low seul -> 0.3")
    check(approx(score_financial_capacity(Prospect(business_size="large")), 1.0), "financial: taille large seule (panier inconnu) -> 1.0")
    check(approx(score_financial_capacity(Prospect(estimated_ticket_size="high", business_size="solo")), 0.65), "financial: moyenne panier high + taille solo -> 0.65")
    check(approx(score_decision_simplicity(Prospect(estimated_decision_complexity="low")), 1.0), "decision: low complexity -> 1.0")
    check(approx(score_current_friction(Prospect(friction_score=FRICTION_SCORE_MAX)), 1.0), "friction: max -> 1.0")
    check(approx(score_current_friction(Prospect(friction_score=0)), 0.0), "friction: 0 -> 0.0")
    check(score_current_friction(Prospect(friction_score=999)) == 1.0, "friction: au-delà du max -> plafonné 1.0")
    check(approx(score_accessibility(Prospect(telephone="01", email="a@b.com")), 0.8), "accessibility: tél + email -> 0.8")
    check(score_accessibility(Prospect()) == 0.0, "accessibility: aucun canal -> 0.0 (connu, pas None)")


def test_subscores_unknown():
    print("test_subscores_unknown")
    check(score_financial_capacity(Prospect(estimated_ticket_size="unknown")) is None, "financial: unknown -> None")
    check(score_decision_simplicity(Prospect(estimated_decision_complexity="unknown")) is None, "decision: unknown -> None")
    check(score_business_maturity(Prospect(activity_signal="unknown", raw_user_ratings_total=0)) is None, "maturity: activité + avis inconnus -> None")
    # digital_need n'est plus None pour "autre" (cf. test_subscores_known : 0.15).
    # current_friction, accessibility et digital_need ne sont donc jamais None.
    check(score_current_friction(Prospect()) is not None, "friction: jamais None")
    check(score_accessibility(Prospect()) is not None, "accessibility: jamais None")
    check(score_digital_need(Prospect()) is not None, "digital_need: jamais None (autre -> 0.15)")


def test_maturity_rating_guard():
    print("test_maturity_rating_guard")
    # raw_rating ne doit PAS compter si le volume d'avis est inconnu (=0).
    check(score_business_maturity(Prospect(activity_signal="unknown", raw_user_ratings_total=0, raw_rating=4.8)) is None, "note ignorée si avis inconnus -> None")
    check(approx(score_business_maturity(Prospect(activity_signal="high", raw_user_ratings_total=300, raw_rating=4.8)), 1.0), "activité high + note 4.8 -> 1.0")
    check(approx(score_business_maturity(Prospect(activity_signal="unknown", raw_user_ratings_total=300, raw_rating=4.8)), 1.0), "activité inconnue + note connue -> note seule")


def test_decision_signal_penalty():
    print("test_decision_signal_penalty")
    check(approx(score_decision_simplicity(Prospect(estimated_decision_complexity="low", network_signal="suspected")), 0.6), "réseau suspecté -> pénalité ×0.6")
    check(approx(score_decision_simplicity(Prospect(estimated_decision_complexity="low", franchise_signal="suspected")), 0.6), "franchise suspectée -> pénalité ×0.6")
    check(approx(score_decision_simplicity(Prospect(estimated_decision_complexity="low", network_signal="no")), 1.0), "aucun soupçon -> pas de pénalité")
    check(approx(score_decision_simplicity(Prospect(estimated_decision_complexity="low")), 1.0), "signaux unknown (défaut) -> pas de pénalité")
    # "confirmed" : garde-fou plus sévère que "suspected", mais JAMAIS 0 (l'exclusion
    # est le rôle explicite des filtres, pas une mise à zéro silencieuse du score).
    conf = score_decision_simplicity(Prospect(estimated_decision_complexity="low", franchise_signal="confirmed"))
    check(conf is not None and 0 < conf < 0.6, f"franchise confirmée -> pénalité forte mais >0 (pas d'exclusion silencieuse) ({conf})")
    # Le plus fort des deux axes l'emporte (plus petit multiplicateur), sans cumul.
    both = score_decision_simplicity(Prospect(estimated_decision_complexity="low", franchise_signal="confirmed", network_signal="suspected"))
    check(approx(both, conf), "signal le plus fort (confirmed) prime sur suspected, sans double pénalité")
    # Pénalité CONFIGURABLE et bornée ]0, 1] -> jamais d'exclusion via le score.
    check(all(0 < v <= 1.0 for v in _DECISION_SIGNAL_PENALTY.values()),
          "multiplicateurs configurables dans ]0,1] (jamais d'exclusion silencieuse)")


# --- Agrégation : bornes, déterminisme, idéal/faible ------------------------


def test_bounds():
    print("test_bounds")
    for p in (Prospect(), ideal_prospect(), Prospect(has_website=True, website_quality="good", has_booking=True, booking_quality="advanced", telephone="01")):
        b = calculate_bps(p).bps
        check(0 <= b <= 100, f"BPS dans [0,100] : {b}")
    check(ideal_prospect().bps == 0, "ideal_prospect non scoré tant que assign_bps n'est pas appelé")


def test_default_anchor():
    print("test_default_anchor")
    # Prospect() : "autre" -> digital_need 0.15 (faible mais CONNU) ; friction 0
    # (non calculée) -> exclusion no_pain_to_solve ; aucun canal -> pénalité
    # no_direct_contact ; financial/decision/maturity inconnues (exclues). Le BPS
    # tombe à 0 via des signaux NÉGATIFS connus (jamais via une dim absente mise à
    # 0) : un objet par défaut sans aucun signal positif n'est jamais promu.
    b = calculate_bps(Prospect()).bps
    check(b == 0, f"Prospect() par défaut -> 0 (pénalité contact + exclusion friction nulle ; obtenu {b})")
    check(b < 75, "Prospect() par défaut reste sous le seuil d'export (jamais promu)")


def test_ideal_and_weak():
    print("test_ideal_and_weak")
    check(calculate_bps(ideal_prospect()).bps == 100, "prospect idéal -> 100")
    weak = Prospect(
        has_website=True, website_quality="good", has_booking=True, booking_quality="advanced",
        estimated_ticket_size="low", estimated_decision_complexity="high",
        activity_signal="low", raw_user_ratings_total=10, raw_rating=2.5,
        telephone="01", friction_score=0,
    )
    wb = calculate_bps(weak).bps
    check(wb <= 30, f"prospect faible -> bas ({wb})")
    check(wb < calculate_bps(ideal_prospect()).bps, "faible < idéal")


def test_determinism():
    print("test_determinism")
    p = ideal_prospect(estimated_ticket_size="medium", activity_signal="medium")
    check(calculate_bps(p).bps == calculate_bps(p).bps, "même prospect -> même BPS")


def test_renormalization_no_penalty():
    print("test_renormalization_no_penalty")
    full = calculate_bps(ideal_prospect()).bps
    # V3 : une dimension inconnue est EXCLUE (score None, jamais 0) et son poids
    # redistribué ; seule la CONFIANCE globale est légèrement discountée. Pour un
    # profil par ailleurs idéal, le discount reste marginal -> score au plafond/tout proche.
    r1 = calculate_bps(ideal_prospect(estimated_ticket_size="unknown"))
    check(r1.bps == full == 100, f"financial inconnue -> score quasi inchangé ({r1.bps})")
    check(r1.breakdown["financial_capacity"]["score"] is None, "dim inconnue -> score None (jamais 0)")
    r2 = calculate_bps(ideal_prospect(estimated_ticket_size="unknown", estimated_decision_complexity="unknown"))
    check(r2.bps >= 95, f"deux dimensions inconnues -> discount marginal, reste très haut ({r2.bps})")
    check(r2.adjustments["confidence"] < 1.0, "couverture partielle -> confiance < 1.0 (discount léger et borné)")


def test_friction_monotonic():
    print("test_friction_monotonic")
    base = dict(
        has_website=True, website_quality="average", has_booking=False,
        estimated_ticket_size="medium", estimated_decision_complexity="medium",
        activity_signal="medium", raw_user_ratings_total=50, raw_rating=4.0,
        telephone="01", email="a@b.com",
    )
    scores = [calculate_bps(Prospect(friction_score=f, **base)).bps for f in range(0, FRICTION_SCORE_MAX + 1)]
    check(all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1)), "BPS non décroissant quand la friction monte")
    check(scores[-1] > scores[0], f"friction max > friction nulle ({scores[0]} -> {scores[-1]})")


# --- assign_bps : écrit bps + bps_breakdown ---------------------------------


def test_assign_bps():
    print("test_assign_bps")
    p = ideal_prospect(estimated_ticket_size="medium")
    result = calculate_bps(p)
    returned = assign_bps(p)
    check(returned is p, "assign_bps retourne le prospect")
    check(p.bps == result.bps, "assign_bps écrit p.bps")
    check(p.bps_breakdown == result.breakdown, "assign_bps écrit p.bps_breakdown")
    check(set(p.bps_breakdown.keys()) == set(BPS_WEIGHTS.keys()), "breakdown = 6 dimensions")
    check("current_friction" in p.bps_breakdown, "dimension renommée current_friction présente")
    check("friction_score" not in p.bps_breakdown, "pas de clé friction_score (évite la confusion)")
    # Idempotent.
    first = p.bps
    assign_bps(p)
    check(p.bps == first, "assign_bps idempotent")
    # Cohérence breakdown : les contributions décomposent la BASE (avant confiance,
    # bonus, pénalité, exclusion) -> leur somme ≈ adjustments["base"], pas le bps final.
    total_contrib = sum(d["contribution"] for d in p.bps_breakdown.values())
    check(abs(total_contrib - result.adjustments["base"]) <= 1.0,
          f"Σ contributions ≈ base ({total_contrib} vs {result.adjustments['base']})")


def test_breakdown_known_flags():
    print("test_breakdown_known_flags")
    bd = calculate_bps(ideal_prospect(estimated_ticket_size="unknown")).breakdown
    check(bd["financial_capacity"]["known"] is False, "financial inconnue -> known False")
    check(bd["financial_capacity"]["score"] is None, "financial inconnue -> score None")
    check(bd["financial_capacity"]["contribution"] == 0.0, "financial inconnue -> contribution 0")
    check(bd["digital_need"]["known"] is True, "digital_need connue -> known True")


# --- Ajustements V3 : confiance, bonus, pénalité, exclusion -----------------


def test_confidence_factor():
    print("test_confidence_factor")
    # Couverture complète (toutes dimensions connues) -> confiance 1.0 (aucun discount).
    check(calculate_bps(ideal_prospect()).adjustments["confidence"] == 1.0,
          "toutes dimensions connues -> confiance 1.0")
    # Signaux partiels -> confiance dans [plancher, 1[ : discount léger et borné.
    sparse = calculate_bps(Prospect(business_type="beaute", friction_score=3, telephone="01"))
    conf = sparse.adjustments["confidence"]
    check(CONFIDENCE_FLOOR <= conf < 1.0, f"signaux partiels -> confiance ∈ [plancher, 1[ ({conf})")
    # La dimension absente reste None dans le breakdown (jamais mise à 0 par la confiance).
    check(sparse.breakdown["financial_capacity"]["score"] is None,
          "dimension inconnue -> score None (la confiance ne l'assimile jamais à 0)")


def test_bonus_rules():
    print("test_bonus_rules")
    adj = calculate_bps(ideal_prospect()).adjustments
    check("metier_fit_with_pain" in adj["bonuses"],
          "métier fort besoin + friction forte -> bonus metier_fit_with_pain")
    check(adj["bonus"] == BONUS_CAP, f"bonus cumulés plafonnés à BONUS_CAP ({adj['bonus']})")
    # Manque concret (ni site ni résa, flags présents) sur un métier à fort besoin.
    gap = calculate_bps(ideal_prospect(friction_flags=["no_website", "no_booking"]))
    check("clear_fixable_gap" in gap.adjustments["bonuses"],
          "ni site ni résa + fort besoin -> bonus clear_fixable_gap")


def test_penalty_rules():
    print("test_penalty_rules")
    # Joignable uniquement via réseaux (ni tél ni email) -> pénalité outbound.
    social = calculate_bps(ideal_prospect(telephone="", email="", instagram="ig"))
    check("no_direct_contact" in social.adjustments["penalties"],
          "ni téléphone ni email -> pénalité no_direct_contact")
    # Réputation faible CONNUE (avis présents, note < 3.5) -> pénalité.
    bad = calculate_bps(ideal_prospect(raw_user_ratings_total=120, raw_rating=2.9))
    check("weak_reputation" in bad.adjustments["penalties"], "note < 3.5 connue -> pénalité weak_reputation")
    # Réputation INCONNUE (aucun avis) -> pas de pénalité (donnée absente ≠ pénalité).
    unknown = calculate_bps(ideal_prospect(raw_user_ratings_total=0, raw_rating=0.0, activity_signal="unknown"))
    check("weak_reputation" not in unknown.adjustments["penalties"],
          "réputation inconnue -> pas de pénalité (donnée absente ≠ pénalité)")


def test_exclusion_rules():
    print("test_exclusion_rules")
    # Aucune friction (rien à corriger) -> exclusion + plafond dur (jamais exporté).
    nopain = calculate_bps(ideal_prospect(friction_score=0))
    check("no_pain_to_solve" in nopain.adjustments["exclusions"], "friction nulle -> exclusion no_pain_to_solve")
    check(nopain.bps <= EXCLUSION_CEIL, f"prospect exclu plafonné à EXCLUSION_CEIL ({nopain.bps})")
    # Établissement non opérationnel -> exclusion (invendable), profil par ailleurs fort.
    closed = calculate_bps(ideal_prospect(raw_business_status="CLOSED_PERMANENTLY"))
    check("inactive_business" in closed.adjustments["exclusions"], "statut non opérationnel -> exclusion inactive_business")
    check(closed.bps <= EXCLUSION_CEIL, "établissement fermé plafonné sous le seuil d'export")
    # Statut opérationnel ou inconnu -> PAS d'exclusion inactive_business.
    check("inactive_business" not in calculate_bps(ideal_prospect(raw_business_status="OPERATIONAL")).adjustments["exclusions"],
          "OPERATIONAL -> pas d'exclusion")
    check("inactive_business" not in calculate_bps(ideal_prospect(raw_business_status="")).adjustments["exclusions"],
          "statut inconnu (vide) -> pas d'exclusion (donnée absente ≠ exclusion)")


def main():
    for test in (
        test_subscores_known,
        test_subscores_unknown,
        test_maturity_rating_guard,
        test_decision_signal_penalty,
        test_bounds,
        test_default_anchor,
        test_ideal_and_weak,
        test_determinism,
        test_renormalization_no_penalty,
        test_friction_monotonic,
        test_assign_bps,
        test_breakdown_known_flags,
        test_confidence_factor,
        test_bonus_rules,
        test_penalty_rules,
        test_exclusion_rules,
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
