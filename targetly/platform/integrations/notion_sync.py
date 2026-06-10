"""Synchronisation idempotente des prospects vers Notion.

Étape "Notion" du pipeline. Clé unique = Google Place ID, stockée dans la
propriété texte « ID Client ».

Contrat (décisions produit) :
- Recherche d'une fiche par ID Client (Place ID).
  - Trouvée  -> UPDATE des seuls champs MOTEUR.
  - Absente  -> CREATE de la fiche complète.
- Ne JAMAIS écraser les champs saisis manuellement (Statut, Notes, Responsable,
  Priorité, commentaires, dates) : l'update ne touche qu'aux champs moteur.
- Champs moteur synchronisés (les seuls remontés dans Notion) :
  Opportunité commerciale (BPS 0-100), Qualification, Niveau de structuration
  (business_maturity 0-100). Friction et autres sous-scores restent calculés
  en interne mais NE SONT PLUS écrits dans Notion (hors structure cible).
- Une dimension inconnue reste VIDE dans Notion (number: None) — jamais convertie en 0.

Les fonctions d'I/O sont au niveau module (monkeypatchables pour les tests).
"""

import requests

from targetly.core.model import Prospect
from targetly.core.segmentation import qualification
from targetly.core.friction import besoins_from_flags
from targetly.core.messaging import messenger_message

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# Dimension du breakdown BPS -> propriété number Notion (valeurs 0-100).
# Seule la maturité business est remontée (colonne « Niveau de structuration ») ;
# les autres sous-scores sont calculés en interne mais non écrits dans Notion.
DIMENSION_TO_PROPERTY = {
    "business_maturity": "Niveau de structuration",
}

# Marqueur (texte BRUT) du bloc de message Messenger inséré dans le CONTENU de la
# page (et non dans une propriété). Sert à la fois à l'idempotence (pas de
# doublon si le sync est relancé) et au respect d'un message déjà rédigé À LA
# MAIN : si un bloc contenant ce marqueur existe déjà, on n'écrit rien.
MESSENGER_MARKER = "Message transmis sur Facebook"


# --- Helpers -----------------------------------------------------------------


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _text(value) -> list:
    """Payload rich_text/title borné à 2000 caractères, jamais None."""
    return [{"text": {"content": (value or "")[:2000]}}]


def _url_prop(value) -> dict:
    return {"url": value or None}


# --- Construction des propriétés ---------------------------------------------


def engine_properties(prospect: Prospect) -> dict:
    """Champs MOTEUR uniquement (utilisés tels quels pour create ET update).

    Aucun champ manuel ici : update_page n'écrit donc jamais Statut, Notes, etc.
    """
    props = {
        "Opportunité commerciale": {"number": int(prospect.bps)},
        "Qualification": {"select": {"name": qualification(prospect.bps)}},
    }
    breakdown = prospect.bps_breakdown or {}
    for dim, prop_name in DIMENSION_TO_PROPERTY.items():
        entry = breakdown.get(dim) or {}
        if entry.get("known") and entry.get("score") is not None:
            props[prop_name] = {"number": round(entry["score"] * 100)}
        else:
            # Donnée absente : on VIDE la cellule, jamais de 0 implicite.
            props[prop_name] = {"number": None}
    return props


def _merged_besoins(prospect: Prospect) -> list:
    """Besoins « Notion » : irritants FACTUELS détectés (friction) d'abord — signaux
    observés, donc plus fiables — puis besoins supposés par Claude, dédupliqués en
    préservant l'ordre. La friction alimente ainsi directement le CRM (traçabilité)."""
    besoins = list(besoins_from_flags(prospect.friction_flags))
    for b in (prospect.besoins or []):
        if b not in besoins:
            besoins.append(b)
    return besoins


def create_properties(prospect: Prospect) -> dict:
    """Jeu complet de propriétés pour une NOUVELLE fiche (manuel initial + moteur)."""
    props = {
        "Nom": {"title": _text(prospect.nom)},
        "Statut": {"select": {"name": "Prospect"}},
        "Source": {"select": {"name": "autres"}},
        "Activités/métiers": {"rich_text": _text(prospect.activites)},
        # Affichage : la prose factuelle (booking_details) prime ; repli sur le
        # token canonique (booking_software) qui, lui, pilote le signal has_booking.
        "Booking software": {"rich_text": _text(prospect.booking_details or prospect.booking_software)},
        "Notes": {"rich_text": _text(prospect.notes)},
        "Besoin / Problème": {"multi_select": [{"name": b} for b in _merged_besoins(prospect)]},
    }
    if prospect.place_id:
        props["ID Client"] = {"rich_text": _text(prospect.place_id)}
    if prospect.telephone:
        props["Téléphone"] = {"phone_number": prospect.telephone}
    if prospect.email:
        props["E-mail"] = {"email": prospect.email}
    if prospect.site_web:
        props["Site web"] = _url_prop(prospect.site_web)
    if prospect.facebook:
        props["Facebook"] = _url_prop(prospect.facebook)
    if prospect.instagram:
        props["Instagram"] = _url_prop(prospect.instagram)
    props.update(engine_properties(prospect))
    return props


# --- I/O Notion (monkeypatchables) -------------------------------------------


def find_page_id_by_place_id(place_id, *, token, db_id):
    """ID de la fiche portant cet ID Client (Place ID), ou None. Sans place_id -> None."""
    if not place_id:
        return None
    url = f"{NOTION_API}/databases/{db_id}/query"
    payload = {
        "filter": {"property": "ID Client", "rich_text": {"equals": place_id}},
        "page_size": 1,
    }
    resp = requests.post(url, headers=_headers(token), json=payload, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


def create_page(prospect: Prospect, *, token, db_id) -> str:
    url = f"{NOTION_API}/pages"
    payload = {"parent": {"database_id": db_id}, "properties": create_properties(prospect)}
    resp = requests.post(url, headers=_headers(token), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()["id"]


def update_page(page_id: str, prospect: Prospect, *, token) -> str:
    """PATCH des seuls champs moteur (les champs manuels restent intacts)."""
    url = f"{NOTION_API}/pages/{page_id}"
    payload = {"properties": engine_properties(prospect)}
    resp = requests.patch(url, headers=_headers(token), json=payload, timeout=15)
    resp.raise_for_status()
    return page_id


# --- Message Messenger dans le CONTENU de la page (best-effort, idempotent) ---


def _paragraph_block(content: str, *, bold: bool = False, italic: bool = False) -> dict:
    """Bloc paragraphe Notion (texte borné à 2000 caractères, jamais None)."""
    rich = {"type": "text", "text": {"content": (content or "")[:2000]}}
    if bold or italic:
        rich["annotations"] = {"bold": bold, "italic": italic}
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [rich]}}


def _messenger_blocks(prospect: Prospect) -> list:
    """Blocs du message : marqueur en gras+italique (équivalent ***…***) puis le
    corps, découpé en paragraphes (rendu lisible et copier-coller direct)."""
    blocks = [_paragraph_block(f"{MESSENGER_MARKER} :", bold=True, italic=True)]
    blocks.extend(_paragraph_block(p) for p in messenger_message(prospect).split("\n\n"))
    return blocks


def _block_plain_text(block: dict) -> str:
    """Texte brut concaténé d'un bloc, tolérant tout type porteur de rich_text."""
    if not isinstance(block, dict):
        return ""
    payload = block.get(block.get("type", ""), {})
    parts = payload.get("rich_text") or payload.get("text") or []
    out = []
    for part in parts:
        if isinstance(part, dict):
            out.append(part.get("plain_text") or (part.get("text") or {}).get("content") or "")
    return "".join(out)


def _marker_present(blocks) -> bool:
    """True si un bloc contient déjà le marqueur (message manuel OU auto)."""
    marker = MESSENGER_MARKER.lower()
    return any(marker in _block_plain_text(b).lower() for b in (blocks or []))


def get_block_children(page_id: str, *, token) -> list:
    """Enfants directs d'une page (1re page de 100 — suffisant pour détecter un
    message déjà présent, qui vit en haut de la fiche)."""
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"
    resp = requests.get(url, headers=_headers(token), timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def append_block_children(page_id: str, blocks: list, *, token) -> None:
    url = f"{NOTION_API}/blocks/{page_id}/children"
    resp = requests.patch(url, headers=_headers(token), json={"children": blocks}, timeout=15)
    resp.raise_for_status()


def sync_messenger_message(page_id: str, prospect: Prospect, *, token) -> bool:
    """Insère le message Messenger dans le CONTENU de la page, SANS doublon et
    SANS écraser un message manuel.

    Best-effort : toute erreur est avalée pour ne JAMAIS casser la redescente —
    les propriétés moteur sont déjà synchronisées à ce stade. Retourne True si un
    bloc a été ajouté, False sinon (déjà présent, ou erreur réseau silencieuse).
    """
    try:
        if _marker_present(get_block_children(page_id, token=token)):
            return False
        append_block_children(page_id, _messenger_blocks(prospect), token=token)
        return True
    except Exception:
        return False


def create_or_update_prospect(prospect: Prospect, *, token, db_id):
    """Synchronise un prospect sans créer de doublon.

    Retourne (action, page_id) avec action ∈ {"updated", "created"}.
    Sans place_id exploitable, la fiche est créée (aucune clé pour dédupliquer).
    """
    page_id = find_page_id_by_place_id(prospect.place_id, token=token, db_id=db_id)
    if page_id:
        update_page(page_id, prospect, token=token)
        sync_messenger_message(page_id, prospect, token=token)
        return ("updated", page_id)
    new_id = create_page(prospect, token=token, db_id=db_id)
    sync_messenger_message(new_id, prospect, token=token)
    return ("created", new_id)
