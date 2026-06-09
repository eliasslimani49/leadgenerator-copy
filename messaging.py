"""Messaging différencié par bande BPS (C3) — angle d'approche + taux de réponse.

Pur et déterministe. Deux usages :
  - `message_angle(bps)` : à quel ANGLE d'approche correspond la bande de
    qualification du prospect (configurable ; pas de copy figé, un angle par
    bande décliné ensuite à la main).
  - `response_rates(outcomes)` : taux de réponse mesuré PAR BANDE à partir des
    issues réelles (C1), pour savoir quelle bande/quel angle convertit -> boucle.

La bande vient de segmentation.qualification (source unique) : on ne réinvente
aucun seuil ici. Aucune écriture, aucun réseau.
"""

from segmentation import QUALIFICATION_VALUES, qualification
from feedback import RESPONDED_OUTCOMES
from booking import BOOKING_PROVIDERS

# Angle d'approche par bande de qualification (CONFIGURABLE). "aucun" = ne pas
# contacter (la bande "Ignorer" n'est pas démarchée).
ANGLE_BY_QUALIFICATION = {
    "Exceptionnel": "preuve_irritant_plus_roi",   # irritant prouvé + valeur chiffrée, contact direct
    "Prioritaire": "irritant_principal",          # on pointe l'irritant n°1 observé
    "Opportunité": "educatif_leger",              # approche pédagogique, faible pression
    "Ignorer": "aucun",                           # hors démarchage
}
ANGLE_DEFAULT = "aucun"


def message_angle(bps):
    """(qualification, angle) déterministes depuis le BPS."""
    q = qualification(bps)
    return q, ANGLE_BY_QUALIFICATION.get(q, ANGLE_DEFAULT)


def response_rates(outcomes):
    """Taux de réponse par bande de qualification, mesuré sur les issues réelles.

    outcomes : {place_id: {"outcome": str, "bps": int|None}} (sortie de C1).
    Retourne {qualif: {"contacted","responded","rate"}}. Un prospect entre dans
    la mesure dès qu'il a été CONTACTÉ (outcome != "none"). `rate` = responded /
    contacted, ou None si aucun contacté (donnée absente ≠ taux de 0).
    """
    bands = {q: {"contacted": 0, "responded": 0, "rate": None} for q in QUALIFICATION_VALUES}
    for rec in (outcomes or {}).values():
        outcome = (rec or {}).get("outcome", "none")
        if outcome == "none":
            continue  # pas encore contacté -> hors mesure (pas un échec)
        band = qualification((rec or {}).get("bps"))
        slot = bands.setdefault(band, {"contacted": 0, "responded": 0, "rate": None})
        slot["contacted"] += 1
        if outcome in RESPONDED_OUTCOMES:
            slot["responded"] += 1
    for slot in bands.values():
        if slot["contacted"]:
            slot["rate"] = round(slot["responded"] / slot["contacted"], 3)
    return bands


# --- Message Messenger de premier contact (prospection terrain) --------------
# Génération PURE et déterministe d'un message personnalisé, à partir des SEULES
# données réellement collectées sur le prospect (duck-typing). Modèle MODULAIRE :
# 7 blocs (salutation, contexte d'écoute, observation factuelle, question ouverte,
# présentation douce, non-démarche, ouverture). Trois fonctions pures pilotent le
# rendu : messenger_angle (angle marketing), confidence_level (prudence du ton) et
# message_quality_score (triage 0-100). Aucune invention : un signal absent retombe
# sur une formulation ouverte ; un outil n'est nommé que s'il appartient à la liste
# FACTUELLE booking.BOOKING_PROVIDERS. À signal identique -> sortie identique
# (traçable, testable, sans réseau ni LLM). Style court, humain, sans jargon SaaS.

# Catégorie métier (model.BusinessType) -> nom au pluriel pour le message.
METIER_PLURIEL = {
    "coach_sportif": "coachs sportifs",
    "coach_nutrition": "nutritionnistes",
    "coach_business": "coachs",
    "bien_etre": "praticiens du bien-être",
    "therapie": "thérapeutes",
    "photographie": "photographes",
    "beaute": "professionnels de la beauté",
}
# Repli quand le métier est inconnu/"autre" : générique crédible, n'affirme rien.
METIER_DEFAUT = "indépendants"

_KNOWN_BOOKING_TOOLS = {p.lower(): p for p in BOOKING_PROVIDERS}

# Angles marketing possibles (un seul retenu par prospect, le plus actionnable).
MESSAGE_ANGLES = (
    "reservation", "centralisation", "simplification",
    "image_pro", "avis_google", "ecoute_terrain",
)
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")


def _metier_pluriel(business_type) -> str:
    return METIER_PLURIEL.get(business_type or "", METIER_DEFAUT)


def _metier_known(prospect) -> bool:
    return (getattr(prospect, "business_type", "") or "") in METIER_PLURIEL


def _greeting(prospect) -> str:
    """Salutation. N'utilise un prénom QUE s'il est explicitement fourni (attribut
    `prenom`) ; sinon « Bonjour, ». On n'extrait jamais un prénom d'un nom
    d'établissement (risque d'erreur > gain de personnalisation)."""
    prenom = (getattr(prospect, "prenom", "") or "").strip()
    return f"Bonjour {prenom}," if prenom else "Bonjour,"


def _known_tool_name(prospect):
    """Nom canonique de l'outil de réservation SEULEMENT s'il correspond à un
    fournisseur connu (signal factuel) ; sinon None — jamais d'outil supposé."""
    if not getattr(prospect, "has_booking", False):
        return None
    raw = (getattr(prospect, "booking_software", "") or "").strip().lower()
    return _KNOWN_BOOKING_TOOLS.get(raw)


def _contact_channels(prospect) -> list:
    """Canaux de contact réellement renseignés (pour l'angle 'centralisation')."""
    out = []
    if (getattr(prospect, "telephone", "") or "").strip():
        out.append("téléphone")
    if (getattr(prospect, "email", "") or "").strip():
        out.append("email")
    if (getattr(prospect, "facebook", "") or "").strip():
        out.append("Facebook")
    if (getattr(prospect, "instagram", "") or "").strip():
        out.append("Instagram")
    return out


def _review_count(prospect) -> int:
    try:
        return int(getattr(prospect, "raw_user_ratings_total", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _good_reviews(prospect) -> bool:
    """Avis Google exploitables : note élevée ET volume suffisant (factuel)."""
    try:
        rating = float(getattr(prospect, "raw_rating", 0.0) or 0.0)
    except (TypeError, ValueError):
        rating = 0.0
    return rating >= 4.5 and _review_count(prospect) >= 20


def _website_issue(prospect):
    """Type de problème de site, sinon None. 'absent' (pas de site), 'erreur'
    (injoignable / 404, via l'attribut optionnel website_unreachable) ou 'date'
    (website_freshness == 'outdated'). Jamais d'affirmation non vérifiable."""
    if not bool(getattr(prospect, "has_website", False)):
        return "absent"
    if bool(getattr(prospect, "website_unreachable", False)):
        return "erreur"
    if getattr(prospect, "website_freshness", "") == "outdated":
        return "date"
    return None


def messenger_angle(prospect) -> str:
    """Angle marketing déterministe, choisi par priorité de friction décroissante :
    site (image_pro) > réservation absente > outil mal réglé (simplification) >
    multi-canal (centralisation) > bons avis (avis_google) > écoute terrain."""
    if _website_issue(prospect):
        return "image_pro"
    if not bool(getattr(prospect, "has_booking", False)):
        return "reservation"
    if getattr(prospect, "booking_quality", "unknown") != "advanced":
        return "simplification"
    if len(_contact_channels(prospect)) >= 2:
        return "centralisation"
    if _good_reviews(prospect):
        return "avis_google"
    return "ecoute_terrain"


def confidence_level(prospect, angle=None) -> str:
    """Confiance dans l'observation -> prudence du ton.
    HIGH : fait direct (site absent, outil connu détecté, avis chiffrés).
    MEDIUM : indice probable mais faillible. LOW : signal faible (écoute pure)."""
    angle = angle or messenger_angle(prospect)
    if angle == "image_pro":
        return "HIGH" if _website_issue(prospect) == "absent" else "MEDIUM"
    if angle == "simplification":
        return "HIGH" if _known_tool_name(prospect) else "MEDIUM"
    if angle == "avis_google":
        return "HIGH"
    if angle == "ecoute_terrain":
        return "LOW"
    return "MEDIUM"


def _observation(prospect, angle) -> str:
    """Bloc 3 — observation factuelle, prudente, adaptée à l'angle et au signal.
    Aucune affirmation d'absence d'outil non vérifiable : on dit « je n'ai pas
    trouvé / je n'ai pas réussi à voir », jamais « vous n'avez pas »."""
    if angle == "image_pro":
        issue = _website_issue(prospect)
        if issue == "absent":
            return "Je n'ai pas trouvé de site à votre nom, sauf erreur de ma part."
        if issue == "erreur":
            return ("Sauf erreur de ma part, votre site ne s'est pas affiché correctement "
                    "au moment où je l'ai ouvert.")
        return "J'ai l'impression que votre site daterait un peu."
    if angle == "reservation":
        return ("J'ai vu que vous aviez déjà un site, mais je n'ai pas trouvé de lien "
                "simple pour réserver un créneau directement.")
    if angle == "simplification":
        tool = _known_tool_name(prospect)
        if tool:
            return (f"J'ai vu que vous utilisiez {tool} pour vos rendez-vous, mais je n'ai "
                    "pas réussi à voir de créneaux disponibles au moment où j'ai regardé.")
        return ("J'ai l'impression que vous avez déjà un système de réservation, mais je ne "
                "l'ai pas trouvé très simple à utiliser de mon côté.")
    if angle == "centralisation":
        return ("J'ai l'impression qu'on peut vous joindre par plusieurs canaux à la fois "
                "(téléphone, réseaux sociaux…).")
    if angle == "avis_google":
        return "J'ai vu que vous aviez de très bons retours de vos clients sur Google."
    # ecoute_terrain — signal faible : on reste très ouvert.
    return "Je découvre tout juste votre activité."


def _question(angle) -> str:
    """Bloc 4 — question ouverte adaptée à la friction (jamais fermée/commerciale)."""
    return {
        "image_pro": "Comment vos clients vous trouvent-ils et prennent-ils rendez-vous avec vous aujourd'hui ?",
        "reservation": "Comment gérez-vous vos prises de rendez-vous en ce moment ? Plutôt par téléphone, par message ?",
        "simplification": "C'est un choix de votre part, ou simplement un outil un peu pénible à régler ?",
        "centralisation": "Comment vous organisez-vous aujourd'hui entre tous ces canaux ?",
        "avis_google": "C'est surtout le bouche-à-oreille qui vous amène vos clients, ou aussi en ligne ?",
        "ecoute_terrain": "Comment vous organisez-vous au quotidien pour gérer vos rendez-vous et vos clients ?",
    }[angle]


def _pitch(metier, angle) -> str:
    """Bloc 5 — présentation douce de Vitryne, sans jargon SaaS, orientée quotidien."""
    tail = {
        "image_pro": "avoir une page simple et claire où leurs clients les trouvent et réservent facilement.",
        "reservation": "gérer plus simplement leurs rendez-vous, sans tout faire à la main.",
        "simplification": "se simplifier le quotidien et passer moins de temps sur la gestion.",
        "centralisation": "regrouper leurs rendez-vous et leurs échanges au même endroit, sans multiplier les outils.",
        "avis_google": "mettre en avant ce que leurs clients pensent d'eux, sans que ça leur prenne du temps.",
        "ecoute_terrain": "se simplifier le quotidien pour se concentrer sur l'essentiel : leurs clients.",
    }[angle]
    return f"Je travaille sur un petit projet pour aider les {metier} à {tail}"


def messenger_message(prospect) -> str:
    """Bloc à bloc, le message Messenger personnalisé et déterministe.

    Lit uniquement des données présentes sur le prospect. Retourne un texte court,
    humain, prêt à copier-coller. Aucun réseau, aucun LLM."""
    metier = _metier_pluriel(getattr(prospect, "business_type", ""))
    ville = (getattr(prospect, "ville", "") or "").strip()
    lieu = f" autour de {ville}" if ville else ""
    angle = messenger_angle(prospect)
    blocks = [
        _greeting(prospect),
        ("Je me permets de vous écrire car j'échange en ce moment avec plusieurs "
         f"{metier}{lieu} pour mieux comprendre comment ils s'organisent au quotidien."),
        _observation(prospect, angle),
        _question(angle),
        _pitch(metier, angle),
        "Je suis surtout en phase d'écoute terrain, sans démarche commerciale.",
        "Au plaisir d'échanger avec vous !",
    ]
    return "\n\n".join(b for b in blocks if b)


def message_quality_score(prospect) -> int:
    """Score INTERNE 0-100 de personnalisation du message (ne bloque jamais la
    génération). Somme de critères factuels indépendants -> triage humain."""
    angle = messenger_angle(prospect)
    score = 0
    if _metier_known(prospect):
        score += 20
    if (getattr(prospect, "ville", "") or "").strip():
        score += 15
    if angle in ("reservation", "image_pro", "simplification"):
        score += 20  # friction actionnable claire
    if _contact_channels(prospect):
        score += 15
    if _known_tool_name(prospect):
        score += 10
    if _review_count(prospect) >= 20:
        score += 10
    score += {"HIGH": 10, "MEDIUM": 5, "LOW": 0}[confidence_level(prospect, angle)]
    return min(score, 100)


def message_quality_label(score) -> str:
    """Libellé de triage à partir du score (>=75 prêt, >=50 à relire, sinon manuel)."""
    if score >= 75:
        return "Prêt à envoyer"
    if score >= 50:
        return "À relire"
    return "À personnaliser manuellement"


def build_messenger_payload(prospect) -> dict:
    """Vue programmatique complète (message + métadonnées de triage), pure et
    déterministe. N'écrit rien : utilisable pour journaliser/qualifier sans
    toucher à Notion. messenger_message reste l'API publique d'insertion."""
    angle = messenger_angle(prospect)
    score = message_quality_score(prospect)
    return {
        "message": messenger_message(prospect),
        "angle": angle,
        "confidence": confidence_level(prospect, angle),
        "quality_score": score,
        "quality_label": message_quality_label(score),
    }
