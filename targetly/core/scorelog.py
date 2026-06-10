"""Journal best-effort des scores BPS (C2) — persistance locale du breakdown.

Notion ne stocke PAS le détail par dimension du BPS ; or la calibration (C2,
calibration.py) en a besoin pour mesurer le pouvoir discriminant de chaque
dimension sur les résultats réels (gagné/perdu). Ce module journalise en local,
SANS jamais bloquer le pipeline, le breakdown de chaque prospect exporté, indexé
par Google Place ID. La jointure ultérieure avec les issues Notion (feedback.py)
se fait par place_id.

Contraintes :
  - Best effort STRICT : toute erreur d'I/O est avalée -> le scoring/pipeline ne
    doit JAMAIS échouer à cause du journal.
  - Fichier JSONL gitignoré (peut contenir des données prospect) : jamais commité.
  - Aucune donnée sensible : uniquement place_id + score/known par dimension.
  - Pur et déterministe hors I/O ; lecture/écriture injectables -> testable.
"""

import json
import os

SCORE_LOG_PATH = ".vitryne_scores.jsonl"  # relatif au cwd, comme monitoring.RUNS_LOG


def score_record(prospect) -> dict:
    """Ligne de journal MINIMALE pour un prospect scoré (place_id + breakdown).

    On ne conserve que ce dont la calibration a besoin (score/known par dimension)
    plus le contexte utile (bps, business_type). Aucun champ libre, aucun secret.
    """
    breakdown = {
        dim: {"score": entry.get("score"), "known": entry.get("known")}
        for dim, entry in (getattr(prospect, "bps_breakdown", None) or {}).items()
    }
    return {
        "place_id": getattr(prospect, "place_id", ""),
        "bps": getattr(prospect, "bps", 0),
        "business_type": getattr(prospect, "business_type", ""),
        "breakdown": breakdown,
    }


def record_score(prospect, *, path=SCORE_LOG_PATH, append=None) -> bool:
    """Journalise un prospect (best effort). Sans place_id -> rien (pas de clé).

    `append` (ligne str -> None) est injectable pour les tests. Toute erreur d'I/O
    est avalée. Retourne True ssi une ligne a effectivement été écrite.
    """
    if not getattr(prospect, "place_id", ""):
        return False
    try:
        line = json.dumps(score_record(prospect), ensure_ascii=False)
        if append is not None:
            append(line)
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception:
        return False


def load_breakdowns(*, path=SCORE_LOG_PATH, read=None) -> dict:
    """{place_id: breakdown} depuis le journal (dernière ligne par place_id gagne).

    `read` (() -> str) est injectable. Fichier absent / ligne corrompue -> ignorés
    silencieusement (best effort : jamais d'exception remontée à l'appelant).
    """
    try:
        if read is not None:
            content = read()
        elif os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        else:
            return {}
    except Exception:
        return {}
    out = {}
    for raw in (content or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError):
            continue
        pid = (rec or {}).get("place_id")
        if pid:
            out[pid] = rec.get("breakdown") or {}
    return out


def build_calibration_samples(breakdowns, outcomes) -> list:
    """Jointure {place_id: breakdown} × {place_id: {"outcome"}} -> samples C2.

    Un échantillon = {"outcome", "breakdown"} pour chaque place_id présent dans
    les DEUX sources : un breakdown sans issue connue (ou l'inverse) n'apporte rien
    à la calibration. Pur, déterministe, aucun I/O.
    """
    samples = []
    for pid, breakdown in (breakdowns or {}).items():
        rec = (outcomes or {}).get(pid)
        if not rec:
            continue
        samples.append({"outcome": rec.get("outcome", "none"), "breakdown": breakdown})
    return samples
