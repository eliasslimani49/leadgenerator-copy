"""Boucle de feedback conversion (C1) — lecture SEULE des issues commerciales.

Lit le statut commercial SAISI MANUELLEMENT dans Notion (champ « Statut »),
indexé par Google Place ID, et en dérive une issue canonique. N'ÉCRIT JAMAIS
dans Notion : les champs manuels (Statut, Notes, …) restent la source de vérité
humaine. Pur et déterministe ; l'I/O réseau est injectable -> testable hors-ligne.

Objectif : fournir des résultats RÉELS (pas des suppositions) à la calibration
des poids BPS (C2) et à la mesure du taux de réponse par bande (C3).
"""

import requests

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# Échelle d'issue canonique, du plus froid au plus chaud (ordre = progression).
OUTCOME_LADDER = ("none", "contacted", "replied", "meeting", "won", "lost")

# Issues comptant comme « a répondu » (taux de réponse C3). "lost" est exclu :
# un prospect perdu peut ne jamais avoir répondu -> on n'inflate pas le taux.
RESPONDED_OUTCOMES = ("replied", "meeting", "won")
# Issues servant la calibration C2 (signal de conversion / non-conversion).
WON_OUTCOMES = ("won",)
LOST_OUTCOMES = ("lost",)

# Mapping statut Notion (select) -> issue canonique, ALIGNÉ sur les 9 valeurs
# RÉELLES du select « Statut » de la base (source de vérité humaine). Tolérant :
# statut inconnu/absent -> "none" (jamais de conversion supposée — donnée
# absente ≠ résultat). « Bêta testeur » et « Proposition envoyée » sont des
# stades profonds non terminaux (ni won ni lost) -> "meeting" (= a répondu).
STATUT_TO_OUTCOME = {
    "Prospect": "none",                 # pas encore contacté
    "Contacté": "contacted",            # démarché, sans réponse
    "Relancé": "contacted",             # relancé, toujours sans réponse
    "Intéressé": "replied",             # a répondu favorablement
    "RDV pris": "meeting",              # rendez-vous obtenu
    "Proposition envoyée": "meeting",   # post-RDV, offre envoyée (non terminal)
    "Bêta testeur": "meeting",          # engagé dans l'essai (ni won ni lost)
    "Gagné": "won",                     # signé
    "Perdu": "lost",                    # closé négatif
}

# Garde-fou anti-dérive : toute valeur du select « Statut » Notion doit être
# explicitement mappée ci-dessus (sinon elle retombe silencieusement en "none"
# et fausse le comptage de conversion). Vérifié par test_feedback.
NOTION_STATUT_OPTIONS = (
    "Prospect", "Contacté", "Relancé", "Intéressé", "RDV pris",
    "Proposition envoyée", "Bêta testeur", "Gagné", "Perdu",
)


def outcome_from_status(statut):
    """Issue canonique depuis un libellé de statut Notion (tolérant)."""
    if not statut or not isinstance(statut, str):
        return "none"
    return STATUT_TO_OUTCOME.get(statut.strip(), "none")


def responded(outcome) -> bool:
    """True si l'issue traduit une réponse du prospect (C3)."""
    return outcome in RESPONDED_OUTCOMES


# --- Lecture tolérante des propriétés Notion --------------------------------


def _read_rich_text(prop):
    if not isinstance(prop, dict):
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    parts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        txt = it.get("plain_text")
        if txt is None:
            txt = (it.get("text") or {}).get("content")
        if txt:
            parts.append(txt)
    return "".join(parts).strip()


def _read_select(prop):
    if not isinstance(prop, dict):
        return ""
    sel = prop.get("select")
    if isinstance(sel, dict):
        return (sel.get("name") or "").strip()
    return ""


def _read_number(prop):
    if not isinstance(prop, dict):
        return None
    return prop.get("number")


def parse_outcomes(pages):
    """{place_id: {"statut","outcome","bps"}} depuis des pages Notion brutes.

    Tolérant : page malformée ignorée ; sans ID Client -> ignorée (pas de clé de
    jointure). Pur (aucun réseau) : c'est le cœur testable de C1.
    """
    out = {}
    for page in pages or []:
        props = (page or {}).get("properties") or {}
        place_id = _read_rich_text(props.get("ID Client"))
        if not place_id:
            continue
        statut = _read_select(props.get("Statut"))
        out[place_id] = {
            "statut": statut,
            "outcome": outcome_from_status(statut),
            "bps": _read_number(props.get("Opportunité commerciale")),
        }
    return out


# --- I/O Notion (lecture seule, injectable) ---------------------------------


def _default_post(token, db_id):
    def _post(body):
        url = f"{NOTION_API}/databases/{db_id}/query"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()
    return _post


def fetch_all_pages(*, token, db_id, post=None, page_size=100):
    """Toutes les pages de la base (pagination). `post` (body -> json) est
    injectable pour les tests : aucune dépendance réseau dans la logique."""
    post = post or _default_post(token, db_id)
    pages, cursor = [], None
    while True:
        body = {"page_size": page_size}
        if cursor:
            body["start_cursor"] = cursor
        data = post(body) or {}
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return pages


def load_outcomes(*, token, db_id, post=None):
    """Issues commerciales {place_id: {...}} lues depuis Notion (lecture seule)."""
    return parse_outcomes(fetch_all_pages(token=token, db_id=db_id, post=post))
