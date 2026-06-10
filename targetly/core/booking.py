"""Détection FACTUELLE d'un outil de réservation en ligne sur un site.

Reconnaît les principaux fournisseurs FR/EU par signature (domaine de widget,
script, iframe) présente dans le HTML. Pur, déterministe, configurable, sans
I/O. Objectif : remplacer la supposition LLM "booking_software" par un signal
mesuré quand il existe.
"""

# Fournisseur -> sous-chaînes révélatrices (domaines de widget/script/iframe),
# en minuscules. Configurable : ajouter un fournisseur = une ligne.
BOOKING_PROVIDERS = {
    "Planity": ["planity.com"],
    "Treatwell": ["treatwell.fr", "treatwell.com", "widget.treatwell"],
    "Doctolib": ["doctolib.fr", "doctolib.com"],
    "Calendly": ["calendly.com"],
    "Resalib": ["resalib.fr"],
    "Resova": ["resova.com", "resova.eu"],
    "Bookeo": ["bookeo.com"],
    "Fresha": ["fresha.com"],
    "Acuity Scheduling": ["acuityscheduling.com", "squarespace-scheduling.com"],
    "SimplyBook": ["simplybook.me", "simplybook.it"],
    "Setmore": ["setmore.com"],
    "Wix Bookings": ["bookings.wixapps.net", "wix.com/bookings"],
    "Crénolib": ["crenolib.fr"],
    "ClicRDV": ["clicrdv.com"],
    "Reservio": ["reservio.com"],
    "YouCanBook.me": ["youcanbook.me"],
    "Timify": ["timify.com"],
}


def detect_booking_provider(html):
    """Nom du 1er fournisseur de réservation détecté dans le HTML, sinon None.

    Insensible à la casse. Recherche sur le HTML BRUT (les widgets vivent dans
    les href/script/iframe, pas dans le texte visible). Aucune supposition :
    sans signature connue -> None (jamais d'invention).
    """
    if not html:
        return None
    low = html.lower()
    for provider, needles in BOOKING_PROVIDERS.items():
        if any(n in low for n in needles):
            return provider
    return None
