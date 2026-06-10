"""Scrape ciblé d'un site : contacts + détection FACTUELLE de réservation.

Consolide l'ancien `scrape_website` (texte, email, réseaux sociaux) et y ajoute
un parcours ciblé des pages contact/réservation internes pour repérer un widget
de booking (Planity, Doctolib, Calendly…) via `booking.detect_booking_provider`.

Signaux factuels d'abord : le booking_software renvoyé ici provient d'une
signature réellement présente dans le HTML, pas d'une supposition LLM.

I/O isolée derrière un getter injectable (`get`) -> testable sans réseau. Tout
échec réseau est silencieux et non bloquant (le lead n'est jamais perdu).
"""

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from targetly.core.booking import detect_booking_provider

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Mots-clés révélateurs d'une page contact/réservation (dans l'URL du lien).
CONTACT_LINK_KEYWORDS = (
    "contact", "rendez-vous", "rendezvous", "rdv", "prendre-rdv",
    "reservation", "réservation", "reserver", "réserver", "booking", "devis",
)
# Plafond de pages internes suivies (quota réseau maîtrisé).
MAX_CONTACT_PAGES = 3
# Plafond de texte conservé pour l'analyse (inchangé vs ancien comportement).
TEXT_LIMIT = 8000

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _default_get(url):
    """Getter HTTP par défaut : renvoie le HTML brut, lève en cas d'échec."""
    resp = requests.get(url, headers=HEADERS_BROWSER, timeout=10)
    resp.raise_for_status()
    return resp.text


# --- Accessibilité du site (sonde légère, prudente, déterministe) ------------
# Distingue : site présent fonctionnel / 404 confirmé / injoignable (timeout,
# SSL, DNS, connexion). N'écrit aucun signal de SCORING — sert uniquement au
# modèle Messenger (image_pro/erreur). Jamais "inaccessible" sur UNE seule
# erreur réseau douteuse : on retente, et on ne force le flag qu'en cas de
# certitude (réponse 404/410 nette, ou échec réseau RÉPÉTÉ).

# Timeout par tentative, en secondes. Constante volontairement isolée pour être
# réglée sans toucher à la logique. Abaissé de 8 à 5 : un site sain répond bien
# en deçà, et cela borne le coût d'attente sur un site lent/mort.
WEBSITE_PROBE_TIMEOUT = 5
# Nombre de tentatives. Appliqué AUX SEULES erreurs réseau ambiguës (timeout /
# SSL / DNS / connexion) : >= 2 pour ne jamais condamner un site sur un unique
# aléa. Les statuts HTTP explicites (404/410, 2xx/3xx, 5xx…) ne sont JAMAIS
# retentés : ils sont définitifs et tranchés dès la 1re réponse.
WEBSITE_PROBE_ATTEMPTS = 2
# Réponses HTTP NETTES valant absence/erreur confirmée de page (pas un aléa réseau).
_CONFIRMED_GONE_STATUS = {404, 410}
# Indices textuels d'un échec de résolution DNS dans une ConnectionError requests.
_DNS_ERROR_TOKENS = (
    "nameresolution", "name or service not known", "getaddrinfo",
    "nodename nor servname", "failed to resolve", "temporary failure in name resolution",
)


def _probe_once(url):
    """Une tentative : code HTTP (int) ou lève une exception requests. HEAD léger
    (pas de corps), suit les redirections, même User-Agent que le scrape."""
    resp = requests.head(url, headers=HEADERS_BROWSER, timeout=WEBSITE_PROBE_TIMEOUT,
                         allow_redirects=True)
    return resp.status_code


def _classify_network_error(exc) -> str:
    """Type d'inaccessibilité réseau à partir de l'exception : timeout / ssl /
    dns / connection (repli prudent)."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl"
    if isinstance(exc, requests.exceptions.ConnectionError):
        text = str(exc).lower()
        if any(tok in text for tok in _DNS_ERROR_TOKENS):
            return "dns"
        return "connection"
    return "connection"


def check_website_accessibility(url, *, probe=None, attempts=WEBSITE_PROBE_ATTEMPTS):
    """Verdict d'accessibilité PRUDENT et déterministe d'un site.

    Retourne {"website_unreachable": True|False|None, "website_unreachable_reason": str} :
      - 404 / 410 (réponse nette du serveur) -> True, reason "404"/"410"
        (aucun retry : ce n'est PAS une erreur réseau douteuse) ;
      - 2xx / 3xx -> False, reason "ok" ;
      - timeout / SSL / DNS / connexion : on RETENTE ; True seulement si TOUTES
        les tentatives échouent (reason = type), sinon le verdict de la tentative
        réussie l'emporte (jamais condamné sur une seule erreur) ;
      - autre statut (5xx, 403, 429, 401, 405…) -> None (INCERTAIN), reason
        "http_<code>" : le serveur répond mais l'état est ambigu, on ne force rien ;
      - URL absente -> None, reason "" (l'absence de site est gérée ailleurs, ce
        n'est pas une inaccessibilité).

    `probe` injectable (url -> code | lève une exception requests) -> tests sans réseau.
    """
    if not url:
        return {"website_unreachable": None, "website_unreachable_reason": ""}
    do_probe = probe or _probe_once
    last_reason = "connection"
    for _ in range(max(1, attempts)):
        try:
            status = do_probe(url)
        except requests.exceptions.RequestException as exc:
            last_reason = _classify_network_error(exc)
            continue  # erreur réseau douteuse -> on retente avant de condamner
        except Exception:
            last_reason = "connection"
            continue
        if status in _CONFIRMED_GONE_STATUS:
            return {"website_unreachable": True, "website_unreachable_reason": str(status)}
        if 200 <= status < 400:
            return {"website_unreachable": False, "website_unreachable_reason": "ok"}
        # Réponse reçue mais ambiguë (5xx transitoire, 403/429 anti-bot, 405…) :
        # on reste prudent, sans jamais forcer le flag.
        return {"website_unreachable": None, "website_unreachable_reason": f"http_{status}"}
    # Toutes les tentatives ont échoué réseau -> inaccessibilité CONFIRMÉE (>= 2 échecs).
    return {"website_unreachable": True, "website_unreachable_reason": last_reason}


def _contact_links(soup, base_url):
    """URLs internes (même domaine) ressemblant à des pages contact/réservation."""
    base_netloc = urlparse(base_url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        low = href.lower()
        if not any(k in low for k in CONTACT_LINK_KEYWORDS):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_netloc:
            continue
        if absolute == base_url or absolute in links:
            continue
        links.append(absolute)
    return links


def scrape_website(url, *, get=None):
    """Scrape le site : contacts + détection de réservation en ligne.

    Retourne toujours le même schéma (clés présentes, valeurs None si absentes) :
      email, facebook, instagram, text, booking_software, booking_url.

    Détection booking : signature dans le HTML de la home, puis (si rien) dans
    jusqu'à MAX_CONTACT_PAGES pages contact/réservation internes. `get`
    injectable pour les tests (url -> HTML brut).
    """
    result = {
        "email": None, "facebook": None, "instagram": None, "text": "",
        "booking_software": None, "booking_url": None,
    }
    if not url:
        return result

    fetch = get or _default_get
    try:
        html = fetch(url)
    except Exception:
        return result
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    result["text"] = text[:TEXT_LIMIT]

    emails = _EMAIL_RE.findall(text)
    if emails:
        result["email"] = emails[0]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href and not result["facebook"]:
            result["facebook"] = href
        if "instagram.com" in href and not result["instagram"]:
            result["instagram"] = href

    # 1) Détection sur la home (widgets/iframes/scripts vivent dans le HTML brut).
    provider = detect_booking_provider(html)
    if provider:
        result["booking_software"] = provider
        result["booking_url"] = url
        return result

    # 2) Sinon, on suit quelques pages contact/réservation internes.
    for link in _contact_links(soup, url)[:MAX_CONTACT_PAGES]:
        try:
            sub_html = fetch(link)
        except Exception:
            continue
        if not sub_html:
            continue
        provider = detect_booking_provider(sub_html)
        if provider:
            result["booking_software"] = provider
            result["booking_url"] = link
            break

    return result
