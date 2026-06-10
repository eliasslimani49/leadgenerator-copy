"""Détection FACTUELLE de franchises et réseaux multi-sites.

Objectif métier : exclure (filtre) ou pénaliser (BPS) les enseignes qui ne
décident pas en local — franchisés, succursales, réseaux nationaux. Ces
prospects n'achètent pas Vitryne (décision centralisée, site déjà fourni).

Pur, déterministe, configurable, sans I/O. Deux niveaux de certitude :
  - "confirmed" : marque d'enseigne reconnue DANS LE NOM de l'établissement
    (signal fort, Google Business) -> exclusion possible en amont.
  - "suspected" : marque trouvée dans le texte du site, ou vocabulaire de
    réseau/franchise -> pénalité de décision, pas exclusion.
  - "no" : texte présent et propre (aucun signal) -> décideur local probable.
  - "unknown" : aucune donnée exploitable -> on ne suppose rien.
"""

# Marques d'enseignes franchisées FR (sous-chaînes minuscules). Une marque dans
# le NOM Google => franchise confirmée. Configurable : une ligne = une enseigne.
FRANCHISE_BRANDS = [
    # Sport / fitness
    "basic fit", "basic-fit", "fitness park", "keep cool", "l'orange bleue",
    "magic form", "neoness", "curves", "cmg sports club",
    # Beauté / coiffure
    "dessange", "camille albane", "jean louis david", "franck provost",
    "saint algue", "tchip", "body minute", "bodyminute", "yves rocher",
    "marionnaud", "nocibé",
]

# Vocabulaire explicite de franchise (recrutement de franchisés sur le site).
FRANCHISE_KEYWORDS = [
    "franchise", "franchisé", "devenez franchisé", "rejoignez le réseau",
    "ouvrir votre franchise", "candidature franchise",
]

# Vocabulaire de réseau multi-sites (plusieurs implantations, pilotage central).
# "rejoignez le réseau" est volontairement présent ici ET dans FRANCHISE_KEYWORDS :
# ce libellé signale à la fois un recrutement de franchisés et l'existence d'un réseau.
NETWORK_KEYWORDS = [
    "nos salons", "nos agences", "nos centres", "trouvez votre salon",
    "réseau national", "présent dans toute la france", "nos établissements",
    "rejoignez le réseau",
]


def detect_franchise_signals(nom, text):
    """(franchise_signal, network_signal) déduits du nom + texte du site.

    Hiérarchie de certitude (du plus fort au plus faible) :
      1. Marque connue dans le NOM Google  -> ("confirmed", "confirmed").
      2. Marque connue dans le TEXTE du site -> ("suspected", "suspected").
      3. Vocabulaire franchise/réseau         -> "suspected" sur l'axe concerné.
      4. Texte présent et propre              -> ("no", "no").
      5. Aucune donnée                        -> ("unknown", "unknown").

    Aucune supposition : sans nom ni texte, on renvoie "unknown" (jamais "no").
    """
    name_low = (nom or "").lower()
    text_low = (text or "").lower()

    # 1. Marque dans le nom -> franchise confirmée (signal le plus fiable).
    if any(b in name_low for b in FRANCHISE_BRANDS):
        return ("confirmed", "confirmed")

    # 2. Marque dans le texte du site -> forte suspicion (pas une preuve).
    if text_low and any(b in text_low for b in FRANCHISE_BRANDS):
        return ("suspected", "suspected")

    # Sans aucun texte exploitable, on ne suppose rien sur le statut réel.
    if not text_low:
        return ("unknown", "unknown")

    # 3. Vocabulaire explicite, axe par axe (factuel, présent dans le contenu).
    franchise_signal = "suspected" if any(k in text_low for k in FRANCHISE_KEYWORDS) else "no"
    network_signal = "suspected" if any(k in text_low for k in NETWORK_KEYWORDS) else "no"
    return (franchise_signal, network_signal)
