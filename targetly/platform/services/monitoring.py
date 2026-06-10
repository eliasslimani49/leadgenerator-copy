"""Monitoring & santé du pipeline Targetly — observabilité simple, opt-in.

Trois usages, tous testables hors-ligne (réseau injectable) :
  - healthcheck(...) : présence des clés .env + accessibilité EN LECTURE SEULE
    de la base Notion (GET /databases/{id}). NE RÉVÈLE JAMAIS la valeur d'une
    clé (seulement présence + longueur, utile pour repérer une clé tronquée).
  - record_run(...)  : journalise les compteurs d'un run en JSONL (append).
    Best effort : ne lève JAMAIS (le monitoring ne doit pas casser un run).
  - run_report(...)  : résumé humain lisible des compteurs d'un run.

Aucune écriture Notion, aucune mutation d'état métier. Déterministe et borné.
"""

import json
import os
import sys
import time

import requests

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# Clés .env requises par le pipeline complet (Google -> Claude -> Notion).
REQUIRED_KEYS = ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "NOTION_TOKEN")

# Journal des runs (JSONL, une ligne par run). Ignoré par git (.gitignore).
RUNS_LOG = ".vitryne_runs.jsonl"

# Valeur par défaut préservée de l'ID de base Notion (jamais en dur ailleurs).
DEFAULT_NOTION_DB_ID = "3468c144ece78081ad5edd03993469a7"


# --- Présence des clés (jamais la valeur) -----------------------------------


def key_presence(env=None) -> dict:
    """État des clés requises SANS jamais exposer leur valeur.

    Retourne {key: {"present": bool, "length": int}}. `env` injectable (défaut
    os.environ) -> testable. On expose la LONGUEUR (diagnostic « clé tronquée »),
    jamais le secret lui-même.
    """
    env = env if env is not None else os.environ
    state = {}
    for key in REQUIRED_KEYS:
        value = env.get(key) or ""
        state[key] = {"present": bool(value), "length": len(value)}
    return state


def missing_keys(env=None) -> list:
    """Liste des clés requises absentes (présence seule, jamais la valeur)."""
    return [k for k, info in key_presence(env).items() if not info["present"]]


# --- Sonde Notion lecture seule (injectable) --------------------------------


def _default_db_probe(token, db_id):
    """Sonde LECTURE SEULE : GET de la base Notion (aucune écriture)."""
    def _probe():
        url = f"{NOTION_API}/databases/{db_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return True
    return _probe


def healthcheck(*, token=None, db_id=None, env=None, db_probe=None) -> dict:
    """Diagnostic de santé : clés présentes + base Notion joignable (lecture seule).

    Aucune valeur de secret n'est lue/affichée. `db_probe` (callable -> bool) est
    injectable pour tester sans réseau. La sonde Notion n'est tentée QUE si un
    token ET un db_id sont disponibles (on ne devine rien).

    Retourne {"healthy": bool, "keys": {...}, "missing_keys": [...],
              "notion_reachable": bool|None, "notion_error": str|None}.
    healthy = aucune clé manquante ET (sonde non tentée OU sonde réussie).
    """
    keys = key_presence(env)
    missing = [k for k, info in keys.items() if not info["present"]]

    token = token if token is not None else (env if env is not None else os.environ).get("NOTION_TOKEN")

    notion_reachable = None
    notion_error = None
    if token and db_id:
        probe = db_probe or _default_db_probe(token, db_id)
        try:
            notion_reachable = bool(probe())
        except Exception as exc:                 # best effort : jamais de crash
            notion_reachable = False
            notion_error = type(exc).__name__

    healthy = (not missing) and (notion_reachable is not False)
    return {
        "healthy": healthy,
        "keys": keys,
        "missing_keys": missing,
        "notion_reachable": notion_reachable,
        "notion_error": notion_error,
    }


def format_health(result) -> str:
    """Rapport de santé lisible. N'imprime JAMAIS la valeur d'une clé."""
    result = result or {}
    lines = ["Santé Targetly : " + ("OK" if result.get("healthy") else "DÉGRADÉ")]
    for key, info in (result.get("keys") or {}).items():
        if info.get("present"):
            lines.append(f"  [OK] {key} : présente (len={info.get('length', 0)})")
        else:
            lines.append(f"  [!!] {key} : ABSENTE")
    reach = result.get("notion_reachable")
    if reach is None:
        lines.append("  [..] Notion : non sondé (token/db_id absent)")
    elif reach:
        lines.append("  [OK] Notion : base joignable (lecture seule)")
    else:
        lines.append(f"  [!!] Notion : injoignable ({result.get('notion_error') or 'erreur'})")
    return "\n".join(lines)


# --- Journalisation des runs (best effort) ----------------------------------


def record_run(counters, *, path=RUNS_LOG, now=None, write=None) -> bool:
    """Journalise un run en JSONL (append). Best effort : ne lève JAMAIS.

    `now` (callable -> float) et `write` (callable: ligne -> None) sont
    injectables pour les tests (aucun fichier touché). Retourne True si la ligne
    a été écrite, False sinon (l'exception est avalée — le monitoring ne casse
    jamais un run).
    """
    record = dict(counters or {})
    record.setdefault("ts", (now or time.time)())
    try:
        line = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    try:
        if write is not None:
            write(line)
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception:
        return False


def run_report(counters) -> str:
    """Résumé humain d'un run à partir de ses compteurs (tolérant aux absents)."""
    c = counters or {}
    return (
        f"Run : {c.get('total', 0)} analysés | {c.get('inserted', 0)} Notion | "
        f"{c.get('rejected', 0)} rejetés | {c.get('cached', 0)} cache | "
        f"{c.get('errors', 0)} erreurs"
    )


# --- Exécutable : healthcheck en ligne de commande --------------------------


def _main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    db_id = os.getenv("NOTION_DB_ID", DEFAULT_NOTION_DB_ID)
    result = healthcheck(token=os.getenv("NOTION_TOKEN"), db_id=db_id)
    print(format_health(result))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    sys.exit(_main())
