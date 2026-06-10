import _path  # noqa: F401 — ajoute la racine du dépôt au sys.path (exécution directe)
"""Tests du cache local des Place ID (cache.py).

Runner autonome, sans dépendance externe :  python3 test_cache.py
Sort en code 1 si au moins un test échoue. Aucune I/O réelle hors tmpdir ;
la logique temporelle est testée avec un `now` injecté (déterministe).
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from targetly.pipeline.cache import (
    load_cache,
    mark_processed,
    save_cache,
    was_processed_recently,
)

_failures = []
NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def test_mark_then_recent():
    print("test_mark_then_recent")
    cache = {}
    mark_processed(cache, "p1", now=NOW, name="Studio X", bps=82)
    check("p1" in cache, "mark_processed indexe le Place ID")
    check(cache["p1"]["name"] == "Studio X" and cache["p1"]["bps"] == 82, "métadonnées conservées")
    check(was_processed_recently(cache, "p1", ttl_days=7, now=NOW) is True, "fraîchement marqué -> récent")
    check(was_processed_recently(cache, "p1", ttl_days=7, now=NOW + timedelta(days=2)) is True, "2 j < 7 j -> récent")


def test_expiry():
    print("test_expiry")
    cache = {}
    mark_processed(cache, "p1", now=NOW)
    check(was_processed_recently(cache, "p1", ttl_days=7, now=NOW + timedelta(days=8)) is False,
          "8 j > 7 j -> expiré (retraité)")
    check(was_processed_recently(cache, "p1", ttl_days=7, now=NOW + timedelta(days=7)) is False,
          "exactement 7 j -> expiré (seuil strict <)")


def test_disabled_and_missing():
    print("test_disabled_and_missing")
    cache = {}
    mark_processed(cache, "p1", now=NOW)
    check(was_processed_recently(cache, "p1", ttl_days=0, now=NOW) is False, "ttl=0 -> cache désactivé")
    check(was_processed_recently(cache, "p1", ttl_days=-1, now=NOW) is False, "ttl négatif -> désactivé")
    check(was_processed_recently(cache, "absent", ttl_days=7, now=NOW) is False, "Place ID absent -> non récent")
    check(was_processed_recently(cache, "", ttl_days=7, now=NOW) is False, "Place ID vide -> non récent")
    check(was_processed_recently({"p2": {"processed_at": "pas une date"}}, "p2", ttl_days=7, now=NOW) is False,
          "horodatage illisible -> non récent (jamais de skip indu)")
    check(was_processed_recently({"p3": "pas un dict"}, "p3", ttl_days=7, now=NOW) is False,
          "entrée malformée -> non récent")


def test_mark_noop_without_id():
    print("test_mark_noop_without_id")
    cache = {}
    mark_processed(cache, "", now=NOW)
    mark_processed(cache, None, now=NOW)
    check(cache == {}, "Place ID vide/None -> aucune entrée créée")


def test_load_save_roundtrip():
    print("test_load_save_roundtrip")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "cache.json")
        check(load_cache(path) == {}, "fichier absent -> {} (tolérant)")
        cache = mark_processed({}, "p1", now=NOW, name="X")
        check(save_cache(cache, path) is True, "save_cache écrit sans erreur")
        reloaded = load_cache(path)
        check(reloaded == cache, "round-trip : relecture identique")
        # Fichier corrompu -> {} (jamais d'exception).
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ json cassé")
        check(load_cache(path) == {}, "JSON corrompu -> {} (jamais lever)")


def test_naive_timestamp_tolerated():
    print("test_naive_timestamp_tolerated")
    # Horodatage sans fuseau (naïf) traité comme UTC, sans planter.
    naive = NOW.replace(tzinfo=None).isoformat()
    cache = {"p1": {"processed_at": naive}}
    check(was_processed_recently(cache, "p1", ttl_days=7, now=NOW + timedelta(days=1)) is True,
          "horodatage naïf -> traité comme UTC (récent)")


def main():
    for test in (
        test_mark_then_recent,
        test_expiry,
        test_disabled_and_missing,
        test_mark_noop_without_id,
        test_load_save_roundtrip,
        test_naive_timestamp_tolerated,
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
