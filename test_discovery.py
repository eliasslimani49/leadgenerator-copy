"""Tests de la découverte élargie (discovery.py).

Runner autonome, SANS réseau ni attente : le getter HTTP (`fetch`) et le délai
inter-pages (`sleep`) sont injectés.
  python3 test_discovery.py   (code 1 si au moins un test échoue)
"""

import sys

from discovery import (
    DISCOVERY_CAP,
    MAX_PAGES_PER_QUERY,
    SOURCE_CONTEXT_KEY,
    _as_list,
    build_queries,
    search_places,
)

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def page(place_ids, *, token=None, status="OK"):
    """Fabrique une réponse Google Text Search factice (1 page)."""
    data = {"status": status,
            "results": [{"place_id": pid, "name": pid} for pid in place_ids]}
    if token:
        data["next_page_token"] = token
    return data


def make_fetch(*responses):
    """Getter factice : renvoie responses[i] au i-ème appel ; au-delà, une page
    OK vide (neutre). Expose .calls et .params_seen pour vérifier sans réseau."""
    seq = list(responses)

    def _fetch(params):
        idx = _fetch.calls
        _fetch.calls += 1
        _fetch.params_seen.append(params)
        return seq[idx] if idx < len(seq) else {"status": "OK", "results": []}

    _fetch.calls = 0
    _fetch.params_seen = []
    return _fetch


def make_sleep():
    def _sleep(_seconds):
        _sleep.calls += 1
    _sleep.calls = 0
    return _sleep


def ids(prefix, n):
    return [f"{prefix}{i}" for i in range(n)]


def test_simple_call_backward_compatible():
    print("test_simple_call_backward_compatible")
    fetch = make_fetch(page(["a", "b", "c"]))  # une page, aucun token
    sleep = make_sleep()
    r = search_places("coiffeur", "Lyon", fetch=fetch, sleep=sleep)
    check([p["place_id"] for p in r] == ["a", "b", "c"], "appel simple -> toutes les places de la page")
    check(fetch.calls == 1, "aucun token -> une seule page récupérée")
    check(sleep.calls == 0, "aucune pagination -> aucun délai")
    first = fetch.params_seen[0]
    check(first.get("query") == "coiffeur Lyon" and first.get("language") == "fr",
          "1re requête = 'keyword zone' en français")


def test_pagination_exceeds_20_caps_50():
    print("test_pagination_exceeds_20_caps_50")
    all_ids = ids("p", 60)
    fetch = make_fetch(
        page(all_ids[0:20], token="t1"),
        page(all_ids[20:40], token="t2"),
        page(all_ids[40:60], token="t3"),
    )
    sleep = make_sleep()
    r = search_places("k", "z", fetch=fetch, sleep=sleep)  # max_results = DISCOVERY_CAP (50)
    check(DISCOVERY_CAP == 50, "plafond global par défaut = 50")
    check(len(r) == 50, f"pagination dépasse 20 et atteint le cap 50 (obtenu {len(r)})")
    check([p["place_id"] for p in r] == all_ids[:50], "les 50 premières places, dans l'ordre")
    check(fetch.calls == 3, "3 pages nécessaires pour atteindre 50 (20+20+10)")
    check(sleep.calls == 2, "un délai entre chaque page enchaînée")
    check("pagetoken" in fetch.params_seen[1], "2e page interrogée via pagetoken")


def test_max_pages_caps_pagination():
    print("test_max_pages_caps_pagination")
    all_ids = ids("p", 80)
    fetch = make_fetch(
        page(all_ids[0:20], token="t1"),
        page(all_ids[20:40], token="t2"),
        page(all_ids[40:60], token="t3"),
        page(all_ids[60:80], token="t4"),  # ne doit JAMAIS être atteinte
    )
    sleep = make_sleep()
    r = search_places("k", "z", fetch=fetch, sleep=sleep, max_results=200)
    check(MAX_PAGES_PER_QUERY == 3, "plafond Google par requête = 3 pages")
    check(fetch.calls == 3, "au plus 3 pages par requête, malgré un token supplémentaire")
    check(len(r) == 60, "3 pages × 20 = 60 (la 4e page n'est pas suivie)")


def test_dedup_across_queries():
    print("test_dedup_across_queries")
    fetch = make_fetch(page(["a", "b", "shared"]), page(["shared", "c"]))
    sleep = make_sleep()
    r = search_places(["k1", "k2"], "z", fetch=fetch, sleep=sleep)
    check([p["place_id"] for p in r] == ["a", "b", "shared", "c"],
          "place_id partagé entre 2 requêtes -> compté une seule fois")
    check(fetch.calls == 2, "une requête par keyword (2 requêtes)")


def test_stats_and_source_context():
    print("test_stats_and_source_context")
    fetch = make_fetch(page(["shared", "a"]), page(["shared", "b"]))
    stats = {}
    r = search_places(["k1", "k2"], "z", fetch=fetch, sleep=make_sleep(), stats=stats)
    shared = next(p for p in r if p["place_id"] == "shared")
    check(stats == {
        "keyword_count": 2, "zone_count": 1,
        "queries_generated": 2, "queries_executed": 2,
        "pages_fetched": 2, "raw_results": 4, "deduped_results": 3,
        "duplicates": 1, "errors": 0,
    }, "stats : saisies, requêtes, résultats bruts et dédupliqués exposés")
    check(shared[SOURCE_CONTEXT_KEY] == [
        {"keyword": "k1", "zone": "z"},
        {"keyword": "k2", "zone": "z"},
    ], "place dédupliquée : toutes les sources restent disponibles en interne")


def test_build_queries():
    print("test_build_queries")
    check(build_queries("a,b", "x, y") == ["a x", "a y", "b x", "b y"],
          "produit cartésien CSV, ordre keyword-majeur")
    check(build_queries("a", "x") == ["a x"], "appel simple -> une requête")
    check(build_queries(["a", "a"], ["x"]) == ["a x"], "requêtes en doublon fusionnées")
    check(build_queries(" a , b ", "x") == ["a x", "b x"], "espaces nettoyés autour des termes")
    check(build_queries("", "x") == [], "aucun keyword -> aucune requête")


def test_as_list():
    print("test_as_list")
    check(_as_list("a, b ,c") == ["a", "b", "c"], "CSV -> liste nettoyée")
    check(_as_list(["a", " b "]) == ["a", "b"], "liste -> nettoyée")
    check(_as_list(None) == [], "None -> liste vide")
    check(_as_list(" , ") == [], "vides écartés")


def test_global_cap_stops_early():
    print("test_global_cap_stops_early")
    fetch = make_fetch(page(["a", "b"]), page(["c", "d"]), page(["e", "f"]))
    sleep = make_sleep()
    r = search_places(["k1", "k2", "k3"], "z", fetch=fetch, sleep=sleep, max_results=3)
    check([p["place_id"] for p in r] == ["a", "b", "c"], "cap global respecté (3) -> s'arrête en plein lot")
    check(fetch.calls == 2, "la 3e requête n'est pas lancée une fois le cap atteint")


def test_max_queries_guard():
    print("test_max_queries_guard")
    fetch = make_fetch(*[page([c]) for c in ids("q", 6)])
    sleep = make_sleep()
    # 3 keywords × 2 zones = 6 requêtes, mais garde-fou à 2.
    r = search_places(["k1", "k2", "k3"], ["z1", "z2"], fetch=fetch, sleep=sleep, max_queries=2)
    check(fetch.calls == 2, "nombre de requêtes borné par max_queries (coût API maîtrisé)")
    check(len(r) == 2, "seules les 2 premières requêtes ont alimenté les résultats")


def test_api_error_non_blocking():
    print("test_api_error_non_blocking")
    fetch = make_fetch(page([], status="REQUEST_DENIED"), page(["c", "d"]))
    sleep = make_sleep()
    r = search_places(["k1", "k2"], "z", fetch=fetch, sleep=sleep)
    check([p["place_id"] for p in r] == ["c", "d"],
          "erreur API sur une requête -> ignorée, les autres requêtes aboutissent")
    check(fetch.calls == 2, "l'erreur n'interrompt que sa propre requête (non bloquant)")


def test_network_error_non_blocking():
    print("test_network_error_non_blocking")
    calls = []

    def fetch(params):
        calls.append(params)
        if len(calls) == 1:
            raise RuntimeError("réseau indisponible")
        return page(["c"])

    stats = {}
    r = search_places(["k1", "k2"], "z", fetch=fetch, sleep=make_sleep(), stats=stats)
    check([p["place_id"] for p in r] == ["c"],
          "exception réseau sur une requête -> les autres requêtes continuent")
    check(len(calls) == 2 and stats["errors"] == 1,
          "exception réseau comptée et bornée à sa requête")


def test_zero_results():
    print("test_zero_results")
    fetch = make_fetch(page([], status="ZERO_RESULTS"))
    sleep = make_sleep()
    r = search_places("k", "z", fetch=fetch, sleep=sleep)
    check(r == [], "ZERO_RESULTS -> liste vide, aucune exception")
    check(fetch.calls == 1 and sleep.calls == 0, "aucune page suivante, aucun délai")


def main():
    for test in (
        test_simple_call_backward_compatible,
        test_pagination_exceeds_20_caps_50,
        test_max_pages_caps_pagination,
        test_dedup_across_queries,
        test_stats_and_source_context,
        test_build_queries,
        test_as_list,
        test_global_cap_stops_early,
        test_max_queries_guard,
        test_api_error_non_blocking,
        test_network_error_non_blocking,
        test_zero_results,
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
