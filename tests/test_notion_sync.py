import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests de la synchronisation Notion (notion_sync.py).

Runner autonome, SANS réseau : les I/O Notion sont remplacées par des fakes
au niveau module (monkeypatch).  python3 test_notion_sync.py
Sort en code 1 si au moins un test échoue.
"""

import sys

from targetly.platform.integrations import notion_sync
from targetly.platform.integrations.notion_sync import (
    engine_properties,
    create_properties,
    create_or_update_prospect,
    MESSENGER_MARKER,
)
from targetly.core.model import Prospect

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def breakdown(**dims):
    """Fabrique un bps_breakdown au format bps.py. score=None => dimension inconnue."""
    out = {}
    for name, score in dims.items():
        if score is None:
            out[name] = {"score": None, "weight": 10, "known": False, "contribution": 0.0}
        else:
            out[name] = {"score": score, "weight": 10, "known": True, "contribution": 0.0}
    return out


class Recorder:
    def __init__(self):
        self.find_calls = []
        self.create_calls = []
        self.update_calls = []


def _patch(*, find=None, create=None, update=None, messenger=None):
    """Remplace les I/O module ; retourne le quadruplet original à restaurer.

    `sync_messenger_message` est neutralisé par défaut (no-op) pour garder les
    tests de routage SANS réseau ; passer `messenger` pour l'observer."""
    saved = (
        notion_sync.find_page_id_by_place_id,
        notion_sync.create_page,
        notion_sync.update_page,
        notion_sync.sync_messenger_message,
    )
    if find is not None:
        notion_sync.find_page_id_by_place_id = find
    if create is not None:
        notion_sync.create_page = create
    if update is not None:
        notion_sync.update_page = update
    notion_sync.sync_messenger_message = messenger if messenger is not None else (lambda *a, **k: False)
    return saved


def _restore(saved):
    (
        notion_sync.find_page_id_by_place_id,
        notion_sync.create_page,
        notion_sync.update_page,
        notion_sync.sync_messenger_message,
    ) = saved


# --- engine_properties : champs moteur uniquement ----------------------------


def test_engine_scaling_and_raw():
    print("test_engine_scaling_and_raw")
    p = Prospect(bps=84, friction_score=5,
                 bps_breakdown=breakdown(business_maturity=0.63))
    props = engine_properties(p)
    check(props["Opportunité commerciale"] == {"number": 84}, "Opportunité commerciale = BPS entier brut")
    check(props["Niveau de structuration"] == {"number": 63}, "0.63 -> 63 (business_maturity 0-100)")
    # Friction et autres sous-scores ne sont plus remontés dans Notion.
    check("Friction Score" not in props, "Friction Score non écrit (hors structure cible)")
    check("Digital Need" not in props and "Financial Capacity" not in props,
          "sous-scores non remontés non écrits")


def test_engine_unknown_is_empty_never_zero():
    print("test_engine_unknown_is_empty_never_zero")
    # business_maturity inconnue (known False) -> cellule vide, jamais 0.
    p = Prospect(bps=70, friction_score=0,
                 bps_breakdown=breakdown(business_maturity=None))
    props = engine_properties(p)
    check(props["Niveau de structuration"] == {"number": None}, "dimension inconnue (known False) -> vide")
    check(props["Niveau de structuration"]["number"] is None, "inconnue = None, jamais 0")
    # Dimension totalement absente du breakdown : traitée comme inconnue, pas 0.
    props_absent = engine_properties(Prospect(bps=70, bps_breakdown=breakdown(digital_need=0.5)))
    check(props_absent["Niveau de structuration"] == {"number": None},
          "dim absente du breakdown -> vide (jamais 0)")


def test_engine_qualification_name():
    print("test_engine_qualification_name")
    check(engine_properties(Prospect(bps=92))["Qualification"] == {"select": {"name": "Exceptionnel"}},
          "BPS 92 -> Qualification Exceptionnel")
    check(engine_properties(Prospect(bps=50))["Qualification"] == {"select": {"name": "Ignorer"}},
          "BPS 50 -> Qualification Ignorer")


def test_engine_has_no_manual_fields():
    print("test_engine_has_no_manual_fields")
    p = Prospect(bps=80, friction_score=2, bps_breakdown=breakdown(business_maturity=0.5))
    props = engine_properties(p)
    for manual in ("Statut", "Notes", "Source", "Nom", "Activités/métiers",
                   "Booking software", "Besoin / Problème", "Téléphone",
                   "E-mail", "Site web", "Facebook", "Instagram", "ID Client"):
        check(manual not in props, f"engine_properties n'inclut pas le champ manuel '{manual}'")
    check(set(props.keys()) == {
        "Opportunité commerciale", "Qualification", "Niveau de structuration",
    }, "engine = exactement les 3 champs moteur remontés dans Notion")


# --- create_properties : fiche complète (manuel initial + moteur) ------------


def test_create_properties_full():
    print("test_create_properties_full")
    p = Prospect(nom="Studio Yoga", bps=88, friction_score=3,
                 activites="cours de yoga", booking_software="Aucun", notes="profil ok",
                 besoins=["Pas de réservation en ligne"], place_id="PID-1",
                 telephone="0102030405", email="a@b.fr", site_web="https://x.fr",
                 bps_breakdown=breakdown(digital_need=0.9))
    props = create_properties(p)
    check(props["Nom"] == {"title": [{"text": {"content": "Studio Yoga"}}]}, "Nom en title")
    check(props["Statut"] == {"select": {"name": "Prospect"}}, "Statut initial = Prospect")
    check(props["Source"] == {"select": {"name": "autres"}}, "Source = autres")
    check(props["ID Client"] == {"rich_text": [{"text": {"content": "PID-1"}}]}, "ID Client écrit (clé de sync)")
    check(props["Téléphone"] == {"phone_number": "0102030405"}, "téléphone présent")
    check(props["E-mail"] == {"email": "a@b.fr"}, "email présent")
    check(props["Site web"] == {"url": "https://x.fr"}, "site web présent")
    check("Facebook" not in props and "Instagram" not in props, "réseaux absents non écrits")
    check(props["Opportunité commerciale"] == {"number": 88}, "champs moteur fusionnés (Opportunité commerciale)")
    check(props["Qualification"] == {"select": {"name": "Prioritaire"}}, "Qualification fusionnée")


def test_create_properties_omits_empty_place_id():
    print("test_create_properties_omits_empty_place_id")
    props = create_properties(Prospect(nom="X", place_id=""))
    check("ID Client" not in props, "place_id vide -> propriété omise")


def test_create_merges_factual_besoins():
    print("test_create_merges_factual_besoins")
    # Irritants FACTUELS (friction) + besoins Claude -> fusionnés, dédupliqués,
    # factuels d'abord (signaux observés plus fiables que la supposition LLM).
    p = Prospect(nom="X", place_id="PID",
                 friction_flags=["no_website", "no_booking"],
                 besoins=["Pas de réservation en ligne", "Pas de paiement en ligne"])
    names = [opt["name"] for opt in create_properties(p)["Besoin / Problème"]["multi_select"]]
    check(names == ["Pas de site", "Pas de réservation en ligne", "Pas de paiement en ligne"],
          "besoins factuels (friction) d'abord, puis Claude, sans doublon")
    # Sans irritant mappé, seuls les besoins Claude remontent (rétrocompatible).
    p2 = Prospect(nom="Y", besoins=["Pas de clients"])
    names2 = [opt["name"] for opt in create_properties(p2)["Besoin / Problème"]["multi_select"]]
    check(names2 == ["Pas de clients"], "aucun irritant mappé -> besoins Claude seuls")


def test_booking_cell_prefers_prose():
    print("test_booking_cell_prefers_prose")
    # Prose présente -> la cellule Notion « Booking software » l'affiche.
    p = Prospect(nom="X", booking_software="Aucun",
                 booking_details="Réservation via un lien Facebook uniquement.")
    props = create_properties(p)
    check(props["Booking software"] == {"rich_text": [{"text": {"content": "Réservation via un lien Facebook uniquement."}}]},
          "prose booking_details affichée dans la cellule Booking software")
    # Prose absente -> repli sur le token canonique (signal has_booking inchangé).
    props2 = create_properties(Prospect(nom="Y", booking_software="Planity", booking_details=""))
    check(props2["Booking software"] == {"rich_text": [{"text": {"content": "Planity"}}]},
          "sans prose -> repli sur le token booking_software")


# --- create_or_update_prospect : décision sans doublon -----------------------


def test_update_when_found():
    print("test_update_when_found")
    rec = Recorder()

    def fake_find(place_id, *, token, db_id):
        rec.find_calls.append(place_id)
        return "PAGE-123"

    def fake_create(prospect, *, token, db_id):
        rec.create_calls.append(prospect)
        return "SHOULD-NOT-HAPPEN"

    def fake_update(page_id, prospect, *, token):
        rec.update_calls.append((page_id, prospect))
        return page_id

    saved = _patch(find=fake_find, create=fake_create, update=fake_update)
    try:
        p = Prospect(nom="X", place_id="PID-9", bps=80)
        action, page_id = create_or_update_prospect(p, token="tok", db_id="db")
        check(action == "updated", "fiche trouvée -> action 'updated'")
        check(page_id == "PAGE-123", "retourne l'id existant")
        check(len(rec.update_calls) == 1, "update appelé une fois")
        check(rec.update_calls[0][0] == "PAGE-123", "update ciblé sur la page trouvée")
        check(len(rec.create_calls) == 0, "create JAMAIS appelé (pas de doublon)")
    finally:
        _restore(saved)


def test_create_when_not_found():
    print("test_create_when_not_found")
    rec = Recorder()

    def fake_find(place_id, *, token, db_id):
        rec.find_calls.append(place_id)
        return None

    def fake_create(prospect, *, token, db_id):
        rec.create_calls.append(prospect)
        return "NEW-456"

    def fake_update(page_id, prospect, *, token):
        rec.update_calls.append((page_id, prospect))
        return page_id

    saved = _patch(find=fake_find, create=fake_create, update=fake_update)
    try:
        p = Prospect(nom="Y", place_id="PID-NEW", bps=75)
        action, page_id = create_or_update_prospect(p, token="tok", db_id="db")
        check(action == "created", "fiche absente -> action 'created'")
        check(page_id == "NEW-456", "retourne le nouvel id")
        check(len(rec.create_calls) == 1, "create appelé une fois")
        check(len(rec.update_calls) == 0, "update non appelé")
    finally:
        _restore(saved)


def test_create_when_no_place_id():
    print("test_create_when_no_place_id")
    rec = Recorder()

    def fake_create(prospect, *, token, db_id):
        rec.create_calls.append(prospect)
        return "NEW-789"

    def fake_update(page_id, prospect, *, token):
        rec.update_calls.append((page_id, prospect))
        return page_id

    # find RÉEL : court-circuite sur place_id vide, sans réseau.
    saved = _patch(create=fake_create, update=fake_update)
    try:
        p = Prospect(nom="Z", place_id="", bps=72)
        action, page_id = create_or_update_prospect(p, token="t", db_id="d")
        check(action == "created", "sans Place ID -> create (aucune clé pour dédupliquer)")
        check(page_id == "NEW-789", "retourne le nouvel id")
        check(len(rec.create_calls) == 1 and len(rec.update_calls) == 0, "create seul, pas d'update")
    finally:
        _restore(saved)


def test_find_empty_place_id_no_network():
    print("test_find_empty_place_id_no_network")
    # requests n'est pas patché : la fonction DOIT court-circuiter avant tout I/O.
    check(notion_sync.find_page_id_by_place_id("", token="t", db_id="d") is None,
          "place_id vide -> None sans appel réseau")


# --- Message Messenger dans le CONTENU de la page ----------------------------


def test_messenger_blocks_format():
    print("test_messenger_blocks_format")
    p = Prospect(nom="Studio Yoga", business_type="coach_sportif", has_website=False)
    blocks = notion_sync._messenger_blocks(p)
    first = blocks[0]["paragraph"]["rich_text"][0]
    check(first["text"]["content"] == f"{MESSENGER_MARKER} :", "1er bloc = marqueur")
    check(first["annotations"] == {"bold": True, "italic": True}, "marqueur en gras+italique (équiv. ***…***)")
    check(len(blocks) >= 2, "corps du message présent (>=1 paragraphe)")
    check(notion_sync._marker_present(blocks), "marqueur détectable dans ses propres blocs (idempotence)")
    body = " ".join(notion_sync._block_plain_text(b) for b in blocks[1:])
    check(MESSENGER_MARKER not in body, "marqueur absent du corps du message")


def test_marker_present_detection():
    print("test_marker_present_detection")
    manual = [{"type": "heading_2", "heading_2": {"rich_text": [
        {"plain_text": "Message transmis sur Facebook :"}]}}]
    check(notion_sync._marker_present(manual) is True, "marqueur détecté dans un bloc manuel (heading)")
    other = [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Notes diverses"}]}}]
    check(notion_sync._marker_present(other) is False, "absent -> False")
    check(notion_sync._marker_present([]) is False and notion_sync._marker_present(None) is False,
          "[] et None tolérés -> False")


def test_sync_appends_when_absent():
    print("test_sync_appends_when_absent")
    appended = []

    def fake_get(page_id, *, token):
        return []  # aucun bloc -> marqueur absent

    def fake_append(page_id, blocks, *, token):
        appended.append((page_id, blocks))

    saved = (notion_sync.get_block_children, notion_sync.append_block_children)
    notion_sync.get_block_children, notion_sync.append_block_children = fake_get, fake_append
    try:
        p = Prospect(nom="X", business_type="therapie", has_website=False)
        added = notion_sync.sync_messenger_message("PAGE-1", p, token="tok")
        check(added is True, "marqueur absent -> bloc ajouté (True)")
        check(len(appended) == 1 and appended[0][0] == "PAGE-1", "append ciblé sur la page")
        first = appended[0][1][0]["paragraph"]["rich_text"][0]["text"]["content"]
        check(first == f"{MESSENGER_MARKER} :", "blocs ajoutés commencent par le marqueur")
    finally:
        notion_sync.get_block_children, notion_sync.append_block_children = saved


def test_sync_skips_when_present():
    print("test_sync_skips_when_present")
    appended = []

    def fake_get(page_id, *, token):
        return [{"type": "paragraph", "paragraph": {"rich_text": [
            {"plain_text": "Message transmis sur Facebook : bonjour"}]}}]

    def fake_append(page_id, blocks, *, token):
        appended.append((page_id, blocks))

    saved = (notion_sync.get_block_children, notion_sync.append_block_children)
    notion_sync.get_block_children, notion_sync.append_block_children = fake_get, fake_append
    try:
        added = notion_sync.sync_messenger_message("PAGE-2", Prospect(nom="X"), token="tok")
        check(added is False, "marqueur déjà présent -> rien ajouté (False)")
        check(len(appended) == 0, "append JAMAIS appelé (pas de doublon, message manuel préservé)")
    finally:
        notion_sync.get_block_children, notion_sync.append_block_children = saved


def test_sync_best_effort_never_raises():
    print("test_sync_best_effort_never_raises")

    def boom_get(page_id, *, token):
        raise RuntimeError("réseau KO")

    saved = (notion_sync.get_block_children, notion_sync.append_block_children)
    notion_sync.get_block_children = boom_get
    try:
        added = notion_sync.sync_messenger_message("PAGE-3", Prospect(nom="X"), token="tok")
        check(added is False, "erreur réseau avalée -> False, aucune exception (redescente préservée)")
    finally:
        notion_sync.get_block_children, notion_sync.append_block_children = saved


def test_create_or_update_calls_messenger():
    print("test_create_or_update_calls_messenger")
    calls = []

    def fake_find(place_id, *, token, db_id):
        return None

    def fake_create(prospect, *, token, db_id):
        return "NEW-1"

    def fake_messenger(page_id, prospect, *, token):
        calls.append(page_id)
        return True

    saved = _patch(find=fake_find, create=fake_create, messenger=fake_messenger)
    try:
        action, page_id = create_or_update_prospect(Prospect(nom="X", place_id="PID"), token="t", db_id="d")
        check(action == "created" and page_id == "NEW-1", "création OK")
        check(calls == ["NEW-1"], "message Messenger déclenché sur la fiche créée")
    finally:
        _restore(saved)


def main():
    for test in (
        test_engine_scaling_and_raw,
        test_engine_unknown_is_empty_never_zero,
        test_engine_qualification_name,
        test_engine_has_no_manual_fields,
        test_create_properties_full,
        test_create_properties_omits_empty_place_id,
        test_create_merges_factual_besoins,
        test_booking_cell_prefers_prose,
        test_update_when_found,
        test_create_when_not_found,
        test_create_when_no_place_id,
        test_find_empty_place_id_no_network,
        test_messenger_blocks_format,
        test_marker_present_detection,
        test_sync_appends_when_absent,
        test_sync_skips_when_present,
        test_sync_best_effort_never_raises,
        test_create_or_update_calls_messenger,
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
