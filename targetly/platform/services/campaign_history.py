"""Historique des campagnes — relecture LECTURE SEULE du journal des runs.

Implémente l'ADR-2026-06-10-campaign-history-local (option 2, V1) : restituer
`.vitryne_runs.jsonl` écrit par monitoring.record_run — qui reste INCHANGÉ —
sous trois vues : derniers runs, agrégats par couple métier x zone, deltas
entre les deux derniers passages d'un même couple.

Module PUR : aucun réseau, aucune écriture, I/O limitée à la lecture du
journal (isolée dans load_runs, tolérante : fichier absent -> vide, ligne
corrompue -> ignorée et COMPTÉE, champ absent -> défaut, jamais d'exception).

Chronologie : le journal est append-only, l'ordre du fichier fait foi
(le champ ts ne sert qu'à l'affichage — un run sans ts reste ordonné).
"""

import json
from datetime import datetime

# Chemin du journal écrit par monitoring.record_run. Dupliqué ici (et vérifié
# par test de parité) plutôt qu'importé : importer monitoring tirerait
# `requests` et casserait la chaîne d'imports 100 % hors ligne de ce module.
RUNS_LOG = ".vitryne_runs.jsonl"

# Compteurs numériques d'un run (défaut 0 si absents — formats historiques).
_COUNTER_FIELDS = ("total", "inserted", "rejected", "cached", "errors")


# --- Lecture (I/O isolée, tolérante) ------------------------------------------


def load_runs(path=RUNS_LOG):
    """Lit le journal JSONL. Retourne (runs normalisés, lignes ignorées).

    Fichier absent ou illisible -> ([], 0) : un historique vide n'est jamais
    une erreur. Ligne vide -> sautée. Ligne corrompue ou non-objet -> ignorée
    et comptée dans `skipped` (visible dans le rapport, jamais de crash).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], 0
    runs, skipped = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(raw, dict):
            skipped += 1
            continue
        runs.append(normalize_run(raw))
    return runs, skipped


def normalize_run(raw) -> dict:
    """Run aux champs garantis (jamais de KeyError sur un format historique)."""
    run = {
        "keyword": str(raw.get("keyword") or ""),
        "city": str(raw.get("city") or ""),
        "ts": None,
    }
    ts = raw.get("ts")
    if isinstance(ts, (int, float)):
        run["ts"] = float(ts)
    for name in _COUNTER_FIELDS:
        value = raw.get(name)
        run[name] = int(value) if isinstance(value, (int, float)) else 0
    return run


# --- Vues pures ----------------------------------------------------------------


def recent_runs(runs, limit=10) -> list:
    """Derniers runs, plus récent d'abord (ordre du fichier = chronologique)."""
    if limit <= 0:
        return []
    return list(reversed(runs[-limit:]))


def _campaign_key(run):
    return (run["keyword"], run["city"])


def aggregate_by_campaign(runs) -> dict:
    """Agrégats par couple (métier, zone) : volumes cumulés et taux d'insertion.

    insert_rate = inserted / total, None si total nul (donnée absente ≠ taux 0,
    même règle que partout dans le pipeline).
    """
    aggregates = {}
    for run in runs:
        slot = aggregates.setdefault(_campaign_key(run), {
            "runs": 0, "last_ts": None,
            **{name: 0 for name in _COUNTER_FIELDS},
        })
        slot["runs"] += 1
        for name in _COUNTER_FIELDS:
            slot[name] += run[name]
        if run["ts"] is not None:
            slot["last_ts"] = run["ts"]  # ordre du fichier -> le dernier vu gagne
    for slot in aggregates.values():
        slot["insert_rate"] = (slot["inserted"] / slot["total"]) if slot["total"] else None
    return aggregates


def campaign_deltas(runs) -> list:
    """Évolution entre les DEUX derniers passages de chaque couple métier x zone.

    Un couple vu une seule fois n'a pas de delta. Retourne une liste triée par
    (métier, zone) de dicts {keyword, city, delta_total, delta_inserted,
    delta_errors} : positif = en hausse au dernier passage.
    """
    by_campaign = {}
    for run in runs:
        by_campaign.setdefault(_campaign_key(run), []).append(run)
    deltas = []
    for (keyword, city), bucket in sorted(by_campaign.items()):
        if len(bucket) < 2:
            continue
        last, previous = bucket[-1], bucket[-2]
        deltas.append({
            "keyword": keyword,
            "city": city,
            "delta_total": last["total"] - previous["total"],
            "delta_inserted": last["inserted"] - previous["inserted"],
            "delta_errors": last["errors"] - previous["errors"],
        })
    return deltas


# --- Rapport humain --------------------------------------------------------------


def _date(ts) -> str:
    if ts is None:
        return "date inconnue"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "date inconnue"


def _signed(value) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_history(runs, skipped=0, limit=10) -> str:
    """Rapport complet lisible : derniers runs, agrégats, deltas, lignes ignorées."""
    if not runs:
        lines = ["Historique : aucun run journalisé (.vitryne_runs.jsonl absent ou vide)."]
        if skipped:
            lines.append(f"  {skipped} ligne(s) illisible(s) ignorée(s).")
        return "\n".join(lines)

    lines = [f"Historique : {len(runs)} run(s) journalisé(s)."]
    if skipped:
        lines.append(f"  {skipped} ligne(s) illisible(s) ignorée(s).")

    lines.append(f"\nDerniers runs (max {limit}) :")
    for run in recent_runs(runs, limit):
        lines.append(
            f"  {_date(run['ts'])}  {run['keyword'] or '?'} x {run['city'] or '?'} — "
            f"{run['total']} analysés | {run['inserted']} Notion | "
            f"{run['rejected']} rejetés | {run['cached']} cache | {run['errors']} erreurs"
        )

    lines.append("\nAgrégats par campagne (métier x zone) :")
    aggregates = aggregate_by_campaign(runs)
    for (keyword, city), slot in sorted(aggregates.items()):
        rate = f"{slot['insert_rate']:.0%}" if slot["insert_rate"] is not None else "—"
        lines.append(
            f"  {keyword or '?'} x {city or '?'} : {slot['runs']} run(s), "
            f"{slot['total']} analysés, {slot['inserted']} insérés (taux {rate}), "
            f"{slot['errors']} erreurs, dernier : {_date(slot['last_ts'])}"
        )

    deltas = campaign_deltas(runs)
    if deltas:
        lines.append("\nÉvolution (2 derniers passages d'un même couple) :")
        for d in deltas:
            lines.append(
                f"  {d['keyword'] or '?'} x {d['city'] or '?'} : "
                f"analysés {_signed(d['delta_total'])}, "
                f"insérés {_signed(d['delta_inserted'])}, "
                f"erreurs {_signed(d['delta_errors'])}"
            )
    return "\n".join(lines)
