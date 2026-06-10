"""Tests du monitoring / healthcheck (monitoring.py).

Runner autonome, SANS réseau : la sonde Notion et l'écriture disque sont
injectées.  python3 test_monitoring.py   (code 1 si au moins un test échoue)

Vérifie en particulier qu'AUCUNE valeur de clé n'est jamais exposée et que la
journalisation des runs est best effort (ne casse jamais un run).
"""

import json
import sys

import monitoring
from monitoring import (
    format_health,
    healthcheck,
    key_presence,
    missing_keys,
    record_run,
    run_report,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


_SECRET = "sk-super-secret-VALUE-1234567890"
_FULL_ENV = {
    "GOOGLE_API_KEY": "google-key-abc",
    "ANTHROPIC_API_KEY": "anthropic-key-def",
    "NOTION_TOKEN": _SECRET,
}


def test_key_presence_no_value():
    print("test_key_presence_no_value")
    state = key_presence(_FULL_ENV)
    check(set(state.keys()) == set(monitoring.REQUIRED_KEYS), "les 3 clés requises sont diagnostiquées")
    check(state["NOTION_TOKEN"]["present"] is True, "clé renseignée -> present True")
    check(state["NOTION_TOKEN"]["length"] == len(_SECRET), "longueur exposée (diagnostic clé tronquée)")
    # La structure ne doit JAMAIS contenir la valeur du secret.
    check(_SECRET not in json.dumps(state), "key_presence n'expose jamais la valeur du secret")
    check(missing_keys(_FULL_ENV) == [], "aucune clé manquante quand tout est renseigné")
    check(missing_keys({"GOOGLE_API_KEY": "x"}) == ["ANTHROPIC_API_KEY", "NOTION_TOKEN"],
          "clés absentes listées (présence seule)")
    check(key_presence({"GOOGLE_API_KEY": ""})["GOOGLE_API_KEY"]["present"] is False,
          "clé vide -> absente")


def test_healthcheck_healthy():
    print("test_healthcheck_healthy")
    res = healthcheck(token="tok", db_id="db", env=_FULL_ENV, db_probe=lambda: True)
    check(res["healthy"] is True, "toutes clés + Notion joignable -> healthy")
    check(res["missing_keys"] == [], "aucune clé manquante")
    check(res["notion_reachable"] is True, "sonde Notion OK")
    check(res["notion_error"] is None, "aucune erreur Notion")


def test_healthcheck_missing_key_no_probe():
    print("test_healthcheck_missing_key_no_probe")
    probed = {"called": False}

    def probe():
        probed["called"] = True
        return True

    env = {"GOOGLE_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}  # NOTION_TOKEN absent
    res = healthcheck(db_id="db", env=env, db_probe=probe)
    check(res["healthy"] is False, "clé manquante -> dégradé")
    check("NOTION_TOKEN" in res["missing_keys"], "NOTION_TOKEN listé comme manquant")
    check(res["notion_reachable"] is None, "sans token -> sonde NON tentée (rien deviné)")
    check(probed["called"] is False, "la sonde réseau n'est pas appelée sans token")


def test_healthcheck_notion_unreachable():
    print("test_healthcheck_notion_unreachable")

    def boom():
        raise RuntimeError("403 Forbidden")

    res = healthcheck(token="tok", db_id="db", env=_FULL_ENV, db_probe=boom)
    check(res["healthy"] is False, "Notion injoignable -> dégradé même si clés présentes")
    check(res["notion_reachable"] is False, "sonde en échec -> reachable False")
    check(res["notion_error"] == "RuntimeError", "type d'erreur capturé (pas de crash)")
    check(_SECRET not in json.dumps(res), "le résultat n'expose jamais le token")


def test_format_health_never_leaks_secret():
    print("test_format_health_never_leaks_secret")
    res = healthcheck(token="tok", db_id="db", env=_FULL_ENV, db_probe=lambda: True)
    out = format_health(res)
    check(_SECRET not in out, "le rapport texte n'imprime JAMAIS la valeur du token")
    check("len=" in out, "le rapport montre la longueur (diagnostic) pas la valeur")
    check("OK" in out, "rapport sain marqué OK")
    degraded = format_health(healthcheck(db_id="db", env={"GOOGLE_API_KEY": "x"}))
    check("DÉGRADÉ" in degraded, "rapport dégradé marqué DÉGRADÉ")


def test_record_run_best_effort():
    print("test_record_run_best_effort")
    captured = []
    ok = record_run({"total": 5, "inserted": 2}, now=lambda: 1234.0, write=captured.append)
    check(ok is True, "écriture injectée -> True")
    check(len(captured) == 1, "une ligne écrite")
    parsed = json.loads(captured[0])
    check(parsed["total"] == 5 and parsed["inserted"] == 2, "compteurs journalisés")
    check(parsed["ts"] == 1234.0, "horodatage injecté repris (déterministe)")

    def raising(_line):
        raise IOError("disque plein")

    out = record_run({"total": 1}, write=raising)
    check(out is False, "échec d'écriture -> False (best effort, aucune exception)")
    # Objet non sérialisable -> False sans lever.
    check(record_run({"x": object()}, write=captured.append) is False,
          "compteurs non sérialisables -> False (jamais de crash)")


def test_run_report():
    print("test_run_report")
    txt = run_report({"total": 12, "inserted": 4, "rejected": 6, "cached": 1, "errors": 1})
    for token in ("12", "4", "6", "1", "erreurs"):
        check(token in txt, f"run_report mentionne '{token}'")
    check("0 erreurs" in run_report({"total": 3}), "compteurs absents -> 0 (tolérant)")


def main():
    for test in (
        test_key_presence_no_value,
        test_healthcheck_healthy,
        test_healthcheck_missing_key_no_probe,
        test_healthcheck_notion_unreachable,
        test_format_health_never_leaks_secret,
        test_record_run_best_effort,
        test_run_report,
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
