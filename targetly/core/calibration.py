"""Calibration déterministe des poids BPS (C2) — proposition, jamais auto-appliquée.

À partir des issues réelles (C1) et du breakdown BPS de chaque prospect, propose
un nouveau jeu de `BPS_WEIGHTS` SANS changer la FORMULE du BPS (qui reste
centrale et explicable). Méthode transparente, sans ML, sans boîte noire :

  Pour chaque dimension, on compare le sous-score moyen des prospects GAGNÉS et
  celui des PERDUS (uniquement là où la dimension est connue). L'écart (« lift »)
  mesure le pouvoir DISCRIMINANT de la dimension. Les poids sont redistribués au
  prorata du lift, puis MÉLANGÉS aux poids actuels (facteur prudent `alpha`).

Garde-fous (qualité > automatisation) :
  - Données insuffisantes (peu de gagnés/perdus) -> poids INCHANGÉS, proposition
    marquée non fiable (jamais d'optimisation prématurée).
  - Une dimension sans assez d'échantillons garde son poids actuel.
  - Sortie = PROPOSITION + rapport détaillé ; `BPS_WEIGHTS` n'est jamais muté.
"""

from targetly.core.bps import BPS_WEIGHTS

# Paramètres CONFIGURABLES de prudence.
CALIB_BLEND = 0.5          # part data-driven dans le mélange (0 = inchangé, 1 = full data)
MIN_WON_TOTAL = 10         # gagnés mini sur tout le lot pour calibrer
MIN_LOST_TOTAL = 10        # perdus mini sur tout le lot pour calibrer
MIN_PER_DIM = 5            # échantillons connus mini par groupe (gagnés/perdus) et par dimension


def _dim_score(breakdown, dim):
    """Sous-score [0,1] de la dimension si connu, sinon None (donnée absente)."""
    entry = (breakdown or {}).get(dim)
    if not isinstance(entry, dict):
        return None
    if not entry.get("known"):
        return None
    score = entry.get("score")
    return score if isinstance(score, (int, float)) else None


def _avg(values):
    return sum(values) / len(values) if values else None


def _round_to_total(weights_float, total, order):
    """Arrondit des poids flottants en entiers de somme EXACTE `total`
    (plus grands restes ; départage stable par ordre des dimensions)."""
    floors = {k: int(weights_float[k]) for k in order}
    remainder = total - sum(floors.values())
    by_frac = sorted(order, key=lambda k: (-(weights_float[k] - int(weights_float[k])), order.index(k)))
    for k in by_frac[:max(0, remainder)]:
        floors[k] += 1
    return floors


def propose_weights(samples, *, blend=CALIB_BLEND, weights=None):
    """Propose un jeu de poids BPS calibré sur les résultats réels.

    samples : liste de dicts {"outcome": str, "breakdown": {dim: {"score","known"}}}.
              `outcome` provient de feedback.py ("won"/"lost"/...).
    Retourne {"proposed_weights", "applied": False, "sufficient_data": bool,
              "report": [par dimension ...]}. Ne MUTE jamais BPS_WEIGHTS.
    """
    current = dict(weights or BPS_WEIGHTS)
    order = list(current.keys())
    samples = samples or []

    won = [s for s in samples if (s or {}).get("outcome") == "won"]
    lost = [s for s in samples if (s or {}).get("outcome") == "lost"]

    # Statistiques par dimension (sur données CONNUES uniquement).
    stats = {}
    for dim in order:
        won_scores = [v for v in (_dim_score(s.get("breakdown"), dim) for s in won) if v is not None]
        lost_scores = [v for v in (_dim_score(s.get("breakdown"), dim) for s in lost) if v is not None]
        avg_won, avg_lost = _avg(won_scores), _avg(lost_scores)
        calibratable = len(won_scores) >= MIN_PER_DIM and len(lost_scores) >= MIN_PER_DIM
        lift = (avg_won - avg_lost) if calibratable else None
        stats[dim] = {
            "n_won": len(won_scores), "n_lost": len(lost_scores),
            "avg_won": round(avg_won, 3) if avg_won is not None else None,
            "avg_lost": round(avg_lost, 3) if avg_lost is not None else None,
            "lift": round(lift, 3) if lift is not None else None,
            "calibratable": calibratable,
        }

    sufficient = len(won) >= MIN_WON_TOTAL and len(lost) >= MIN_LOST_TOTAL
    cal_dims = [d for d in order if stats[d]["calibratable"]] if sufficient else []

    if not cal_dims:
        # Pas assez de données fiables -> on ne touche à rien (proposition = actuel).
        report = [{"dimension": d, "old_weight": current[d], "new_weight": current[d],
                   "status": "kept (insufficient data)", **stats[d]} for d in order]
        return {"proposed_weights": dict(current), "applied": False,
                "sufficient_data": sufficient, "report": report}

    # Masse de poids à redistribuer = somme des poids actuels des dimensions calibrables.
    mass = sum(current[d] for d in cal_dims)
    powers = {d: max(0.0, stats[d]["lift"]) for d in cal_dims}
    total_power = sum(powers.values())

    proposed_float = {}
    for d in order:
        if d not in cal_dims:
            proposed_float[d] = float(current[d])         # dimension non calibrable -> inchangée
            continue
        data_weight = (mass * powers[d] / total_power) if total_power > 0 else float(current[d])
        proposed_float[d] = (1 - blend) * current[d] + blend * data_weight

    proposed = _round_to_total(proposed_float, round(sum(current.values())), order)

    report = []
    for d in order:
        status = "calibrated" if d in cal_dims else "kept (insufficient data)"
        report.append({"dimension": d, "old_weight": current[d], "new_weight": proposed[d],
                       "status": status, **stats[d]})
    return {"proposed_weights": proposed, "applied": False,
            "sufficient_data": True, "report": report}


def format_report(result) -> str:
    """Rapport lisible (humain) de la proposition de calibration."""
    lines = []
    flag = "FIABLE" if result.get("sufficient_data") else "NON FIABLE (données insuffisantes)"
    lines.append(f"Calibration BPS — proposition {flag} (jamais appliquée automatiquement)")
    lines.append(f"{'dimension':<20}{'ancien':>7}{'nouveau':>8}{'lift':>8}  statut")
    for row in result.get("report", []):
        lift = row.get("lift")
        lift_s = f"{lift:+.3f}" if lift is not None else "   —"
        lines.append(f"{row['dimension']:<20}{row['old_weight']:>7}{row['new_weight']:>8}{lift_s:>8}  {row['status']}")
    return "\n".join(lines)
