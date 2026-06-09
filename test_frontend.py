"""Tests de cohérence du frontend (templates/index.html) avec le backend.

Runner autonome, sans réseau ni navigateur :  python3 test_frontend.py
Sort en code 1 si au moins un test échoue.

Ces tests verrouillent le contrat UI <-> pipeline pour éviter les régressions :
  - bijection des événements SSE (app.py émet == index.html gère) ;
  - intégrité du DOM (tout getElementById littéral pointe sur un id existant) ;
  - absence de dérive des bandes de qualification (JS == segmentation.py) ;
  - structure minimale (un seul #app, un <script>, un cas default).
"""

import re
import sys
from pathlib import Path

import segmentation

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


# --- Bijection des événements SSE --------------------------------------------


def _emitted_event_types():
    return set(re.findall(r'"type"\s*:\s*"([a-z_]+)"', APP))


def _handled_event_cases():
    # Le switch handleEvent est le seul à utiliser `case '<type>'`.
    return set(re.findall(r"case '([a-z_]+)'", HTML))


def test_event_bijection():
    print("test_event_bijection")
    emitted = _emitted_event_types()
    handled = _handled_event_cases()
    not_handled = sorted(emitted - handled)
    not_emitted = sorted(handled - emitted)
    check(not_handled == [], f"tout événement émis est géré (orphelins backend : {not_handled})")
    check(not_emitted == [], f"tout case géré est réellement émis (cases morts : {not_emitted})")
    check(emitted == handled, "bijection exacte émis <-> gérés")
    # Garde-fou : les événements moteur du lot 6 doivent être présents des deux côtés.
    for must in ("eliminated", "friction_done", "bps_done", "notion_error"):
        check(must in emitted and must in handled, f"événement '{must}' câblé des deux côtés")


# --- Intégrité du DOM --------------------------------------------------------


def test_dom_integrity():
    print("test_dom_integrity")
    ids = set(re.findall(r'id="([\w-]+)"', HTML))
    refs = set(re.findall(r"getElementById\('([\w-]+)'\)", HTML))
    dangling = sorted(refs - ids)
    check(dangling == [], f"aucun getElementById littéral orphelin (manquants : {dangling})")
    # Ids structurels manipulés dynamiquement (NODE_MAP / CONN_MAP) : non couverts
    # par le scan littéral, vérifiés explicitement.
    structural = [
        "node-google", "node-details", "node-scrape", "node-claude", "node-notion",
        "conn-0", "conn-1", "conn-2", "conn-3",
    ]
    for sid in structural:
        check(sid in ids, f"id structurel '{sid}' présent")
    # Ids du moteur. Le BPS est l'anneau hero (score-*), piloté par bps_done ;
    # la rangée moteur porte la bande de qualification + la friction.
    for mid in ("moteur-row", "score-wrap", "score-fill", "score-num",
                "qualif-chip", "friction-val",
                "stat-eliminated", "done-eliminated",
                "stat-cached", "done-cached", "done-top"):
        check(mid in ids, f"id moteur '{mid}' présent")


# --- Anti-dérive des bandes de qualification ---------------------------------


def _js_qualif_bands():
    m = re.search(r"const QUALIF_BANDS\s*=\s*(\[\[.*?\]\]);", HTML, re.DOTALL)
    if not m:
        return None
    pairs = re.findall(r"\[\s*(\d+)\s*,\s*'([^']+)'\s*\]", m.group(1))
    return tuple((int(t), label) for t, label in pairs)


def test_qualification_no_drift():
    print("test_qualification_no_drift")
    js_bands = _js_qualif_bands()
    check(js_bands is not None, "QUALIF_BANDS trouvé et parsable dans le JS")
    check(js_bands == segmentation.QUALIFICATION_BANDS,
          f"JS QUALIF_BANDS == segmentation.QUALIFICATION_BANDS ({js_bands} vs {segmentation.QUALIFICATION_BANDS})")
    # Le défaut JS (isNaN / sous le plancher) doit valoir le défaut backend.
    check(f"return '{segmentation.QUALIFICATION_DEFAULT}'" in HTML,
          f"défaut JS == QUALIFICATION_DEFAULT ('{segmentation.QUALIFICATION_DEFAULT}')")


def test_qualif_classes_cover_all_labels():
    print("test_qualif_classes_cover_all_labels")
    # qualifClass doit mapper exactement les 4 libellés du contrat ; et chaque
    # classe CSS associée doit exister dans la feuille de style.
    m = re.search(r"function qualifClass\(label\)\s*\{(.*?)\}\[label\]", HTML, re.DOTALL)
    check(m is not None, "fonction qualifClass parsable")
    mapping = dict(re.findall(r"'([^']+)':\s*'([a-z]+)'", m.group(1))) if m else {}
    check(set(mapping.keys()) == set(segmentation.QUALIFICATION_VALUES),
          f"qualifClass mappe exactement les 4 libellés ({sorted(mapping.keys())})")
    for css_class in mapping.values():
        check(f".qualif-chip.{css_class}" in HTML, f"classe CSS '.qualif-chip.{css_class}' définie")


# --- Contrat de champs des nouveaux handlers ---------------------------------


def test_engine_handlers_read_correct_fields():
    print("test_engine_handlers_read_correct_fields")
    # friction_done porte le score sous la clé `score` (pas friction_score).
    check("setFriction(d.score)" in HTML, "friction_done lit d.score")
    check("setBps(d.bps)" in HTML, "bps_done lit d.bps")
    # inserted distingue update vs create via d.action.
    check("d.action === 'updated'" in HTML, "inserted/feed dérive le verbe de d.action")
    # eliminated incrémente bien son propre compteur.
    check("stats.eliminated++" in HTML, "eliminated incrémente stats.eliminated")
    # done réconcilie les échecs (total - inserted - skipped - eliminated - cached).
    check(re.search(r"d\.total\s*-\s*d\.inserted\s*-\s*d\.skipped\s*-\s*elim\s*-\s*cached", HTML) is not None,
          "done réconcilie les échecs Notion (cache déduit)")
    # cached_skip alimente le compteur cache.
    check("stats.cached" in HTML, "cached_skip incrémente stats.cached")


# --- Structure minimale ------------------------------------------------------


def test_structure():
    print("test_structure")
    check(HTML.count('id="app"') == 1, "un seul conteneur #app")
    check(HTML.count("<script>") == 1 and HTML.count("</script>") == 1, "un unique bloc <script>")
    check(HTML.count("function handleEvent") == 1, "handleEvent défini une fois")
    check(re.search(r"\bdefault:\s*\n\s*console\.debug", HTML) is not None,
          "handleEvent a un cas default (événement inconnu non bloquant)")
    check("stats = { total: 0, inserted: 0, skipped: 0, eliminated: 0, cached: 0 }" in HTML,
          "stats réinitialise les 5 compteurs (dont eliminated + cached)")


def main():
    for test in (
        test_event_bijection,
        test_dom_integrity,
        test_qualification_no_drift,
        test_qualif_classes_cover_all_labels,
        test_engine_handlers_read_correct_fields,
        test_structure,
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
