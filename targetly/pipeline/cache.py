"""Cache local des Place ID déjà traités (anti-retraitement).

Objectif métier : ne pas re-scraper / re-analyser / re-pousser un prospect vu
il y a moins de N jours. Économise quota Google, tokens Claude et écritures
Notion, sans rien changer à la qualité des leads.

Stockage : un simple JSON local, keyed par Place ID, jamais commité. Pur côté
logique (fonctions testables sans I/O via `now` injectable) ; I/O isolée dans
load_cache / save_cache, tolérante aux pannes (jamais d'exception remontée).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CACHE_PATH = Path(__file__).parent / ".vitryne_cache.json"
# TTL configurable. 0 (ou négatif) => cache désactivé (rien n'est jamais "récent").
CACHE_TTL_DAYS = int(os.getenv("CACHE_TTL_DAYS", "7"))


def _now():
    return datetime.now(timezone.utc)


def load_cache(path=CACHE_PATH):
    """Charge le cache JSON. Tolérant : tout problème -> dict vide (jamais lever)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_cache(data, path=CACHE_PATH):
    """Écrit le cache JSON. Tolérant : échec d'écriture silencieux (non bloquant)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        return True
    except (OSError, TypeError):
        return False


def was_processed_recently(cache, place_id, ttl_days=CACHE_TTL_DAYS, now=None):
    """True si place_id a été traité il y a moins de ttl_days.

    ttl_days <= 0 -> cache désactivé : renvoie toujours False (tout est retraité).
    Entrée absente ou horodatage illisible -> False (on retraite, jamais de skip
    indu). `now` injectable pour des tests déterministes.
    """
    if not place_id or ttl_days <= 0:
        return False
    entry = cache.get(place_id)
    if not isinstance(entry, dict):
        return False
    stamp = entry.get("processed_at")
    if not stamp:
        return False
    try:
        seen = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    current = now or _now()
    age_days = (current - seen).total_seconds() / 86400.0
    return age_days < ttl_days


def mark_processed(cache, place_id, *, now=None, **meta):
    """Enregistre place_id comme traité maintenant (+ métadonnées libres).

    Mutation en place du dict `cache` (renvoyé pour chaînage). Sans place_id,
    no-op : on n'indexe jamais une entrée vide.
    """
    if not place_id:
        return cache
    current = now or _now()
    entry = {"processed_at": current.isoformat()}
    entry.update(meta)
    cache[place_id] = entry
    return cache
