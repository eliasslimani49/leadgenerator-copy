"""Tests du scrape ciblé contact/réservation (scraper.py).

Runner autonome, SANS réseau : le getter HTTP est injecté (url -> HTML).
  python3 test_scraper.py   (code 1 si au moins un test échoue)
"""

import sys

import requests

from scraper import MAX_CONTACT_PAGES, scrape_website, check_website_accessibility

_failures = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


def make_get(pages, *, calls=None):
    """Fabrique un getter HTTP factice à partir d'un dict {url: html}."""
    def _get(url):
        if calls is not None:
            calls.append(url)
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]
    return _get


def test_home_contacts_extracted():
    print("test_home_contacts_extracted")
    home = ("<html><body>Contact : hello@site.fr "
            '<a href="https://facebook.com/site">FB</a> '
            '<a href="https://instagram.com/site">IG</a></body></html>')
    r = scrape_website("https://site.fr/", get=make_get({"https://site.fr/": home}))
    check(r["email"] == "hello@site.fr", "email extrait du texte")
    check(r["facebook"] == "https://facebook.com/site", "lien Facebook extrait")
    check(r["instagram"] == "https://instagram.com/site", "lien Instagram extrait")
    check(r["booking_software"] is None and r["booking_url"] is None, "aucun widget -> booking None")
    check("hello@site.fr" in r["text"], "texte de la home conservé")


def test_booking_detected_on_home():
    print("test_booking_detected_on_home")
    home = '<a href="https://www.planity.com/le-salon">Prendre RDV</a>'
    r = scrape_website("https://site.fr/", get=make_get({"https://site.fr/": home}))
    check(r["booking_software"] == "Planity", "widget Planity sur la home -> détecté")
    check(r["booking_url"] == "https://site.fr/", "booking_url = home quand détecté sur la home")


def test_booking_followed_to_contact_page():
    print("test_booking_followed_to_contact_page")
    pages = {
        "https://site.fr/": '<a href="/contact">Contact / réservation</a>',
        "https://site.fr/contact": '<iframe src="https://calendly.com/cabinet/30min"></iframe>',
    }
    calls = []
    r = scrape_website("https://site.fr/", get=make_get(pages, calls=calls))
    check(r["booking_software"] == "Calendly", "widget sur la page contact -> détecté en suivant le lien")
    check(r["booking_url"] == "https://site.fr/contact", "booking_url = page contact suivie")
    check("https://site.fr/contact" in calls, "la page contact a bien été récupérée")


def test_external_links_not_followed():
    print("test_external_links_not_followed")
    pages = {
        "https://site.fr/": '<a href="https://autre-domaine.com/contact">Contact</a>',
        "https://autre-domaine.com/contact": '<a href="planity.com">x</a>',
    }
    calls = []
    r = scrape_website("https://site.fr/", get=make_get(pages, calls=calls))
    check(r["booking_software"] is None, "lien contact hors domaine -> non suivi, booking None")
    check("https://autre-domaine.com/contact" not in calls, "domaine externe jamais récupéré (même domaine seulement)")


def test_contact_pages_capped():
    print("test_contact_pages_capped")
    home = "".join(f'<a href="/contact-{i}">Contact {i}</a>' for i in range(1, 6))
    pages = {"https://site.fr/": home}
    # Widget placé au-delà du plafond -> ne doit PAS être trouvé.
    for i in range(1, 6):
        body = '<a href="https://fresha.com/x">RDV</a>' if i == 5 else "<p>rien</p>"
        pages[f"https://site.fr/contact-{i}"] = body
    calls = []
    r = scrape_website("https://site.fr/", get=make_get(pages, calls=calls))
    sub_calls = [c for c in calls if "/contact-" in c]
    check(len(sub_calls) <= MAX_CONTACT_PAGES, f"au plus {MAX_CONTACT_PAGES} pages internes suivies (obtenu {len(sub_calls)})")
    check(r["booking_software"] is None, "widget au-delà du plafond -> non détecté (quota maîtrisé)")


def test_no_url_and_network_failure():
    print("test_no_url_and_network_failure")
    empty = scrape_website("", get=make_get({}))
    check(empty == {"email": None, "facebook": None, "instagram": None, "text": "",
                    "booking_software": None, "booking_url": None},
          "URL vide -> schéma complet par défaut")
    # Échec réseau sur la home -> schéma par défaut, jamais d'exception.
    failed = scrape_website("https://down.fr/", get=make_get({}))
    check(failed["text"] == "" and failed["booking_software"] is None, "échec réseau -> résultat neutre (non bloquant)")


# --- Sonde d'accessibilité du site (check_website_accessibility) -------------


def make_probe(*outcomes):
    """Probe factice déterministe : chaque appel consomme un outcome
    (int -> code HTTP renvoyé ; exception -> levée). Le dernier est répété
    au-delà de la liste. Expose .calls pour vérifier les retries (sans réseau)."""
    seq = list(outcomes)

    def _probe(url):
        _probe.calls += 1
        item = seq[min(_probe.calls - 1, len(seq) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    _probe.calls = 0
    return _probe


def test_probe_reachable_200():
    print("test_probe_reachable_200")
    p = make_probe(200)
    r = check_website_accessibility("https://site.fr/", probe=p)
    check(r == {"website_unreachable": False, "website_unreachable_reason": "ok"}, "200 -> joignable (ok)")
    check(p.calls == 1, "200 -> une seule requête (aucun retry inutile)")


def test_probe_404_confirmed():
    print("test_probe_404_confirmed")
    p = make_probe(404)
    r = check_website_accessibility("https://site.fr/", probe=p, attempts=2)
    check(r["website_unreachable"] is True and r["website_unreachable_reason"] == "404",
          "404 -> inaccessible confirmé, raison '404'")
    check(p.calls == 1, "404 -> aucun retry (réponse nette, pas une erreur réseau douteuse)")


def test_probe_410_confirmed():
    print("test_probe_410_confirmed")
    r = check_website_accessibility("https://site.fr/", probe=make_probe(410))
    check(r["website_unreachable"] is True and r["website_unreachable_reason"] == "410",
          "410 Gone -> inaccessible (raison '410')")


def test_probe_timeout_repeated_unreachable():
    print("test_probe_timeout_repeated_unreachable")
    p = make_probe(requests.exceptions.Timeout("timed out"))
    r = check_website_accessibility("https://site.fr/", probe=p, attempts=2)
    check(r["website_unreachable"] is True and r["website_unreachable_reason"] == "timeout",
          "timeout sur TOUTES les tentatives -> inaccessible (timeout)")
    check(p.calls == 2, "timeout -> retenté (>= 2 tentatives avant de condamner)")


def test_probe_single_error_not_condemned():
    print("test_probe_single_error_not_condemned")
    # Une erreur réseau douteuse PUIS une réponse OK -> jamais condamné.
    p = make_probe(requests.exceptions.Timeout("timed out"), 200)
    r = check_website_accessibility("https://site.fr/", probe=p, attempts=2)
    check(r["website_unreachable"] is False, "1 erreur réseau puis 200 -> JAMAIS condamné (joignable)")
    check(p.calls == 2, "la 2e tentative a bien été effectuée")


def test_probe_ssl_dns_connection():
    print("test_probe_ssl_dns_connection")
    ssl = check_website_accessibility(
        "https://site.fr/", probe=make_probe(requests.exceptions.SSLError("certificate verify failed")), attempts=2)
    check(ssl["website_unreachable"] is True and ssl["website_unreachable_reason"] == "ssl",
          "erreur SSL répétée -> inaccessible (ssl)")
    dns_exc = requests.exceptions.ConnectionError(
        "Failed to resolve 'x.fr' ([Errno -2] Name or service not known)")
    dns = check_website_accessibility("https://site.fr/", probe=make_probe(dns_exc), attempts=2)
    check(dns["website_unreachable"] is True and dns["website_unreachable_reason"] == "dns",
          "échec DNS répété -> inaccessible (dns)")
    conn = check_website_accessibility(
        "https://site.fr/", probe=make_probe(requests.exceptions.ConnectionError("Connection refused")), attempts=2)
    check(conn["website_unreachable"] is True and conn["website_unreachable_reason"] == "connection",
          "connexion refusée répétée -> inaccessible (connection)")


def test_probe_uncertain_status_not_forced():
    print("test_probe_uncertain_status_not_forced")
    p = make_probe(503)
    r = check_website_accessibility("https://site.fr/", probe=p)
    check(r["website_unreachable"] is None, "5xx -> INCERTAIN : le flag n'est jamais forcé")
    check(r["website_unreachable_reason"] == "http_503", "raison interne 'http_503' (traçabilité)")
    check(p.calls == 1, "réponse reçue -> pas de retry")


def test_probe_absent_url_not_unreachable():
    print("test_probe_absent_url_not_unreachable")
    p = make_probe(200)
    r = check_website_accessibility("", probe=p)
    check(r["website_unreachable"] is None, "URL absente -> None (absence de site != inaccessibilité)")
    check(p.calls == 0, "URL absente -> aucune requête réseau")


def main():
    for test in (
        test_home_contacts_extracted,
        test_booking_detected_on_home,
        test_booking_followed_to_contact_page,
        test_external_links_not_followed,
        test_contact_pages_capped,
        test_no_url_and_network_failure,
        test_probe_reachable_200,
        test_probe_404_confirmed,
        test_probe_410_confirmed,
        test_probe_timeout_repeated_unreachable,
        test_probe_single_error_not_condemned,
        test_probe_ssl_dns_connection,
        test_probe_uncertain_status_not_forced,
        test_probe_absent_url_not_unreachable,
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
