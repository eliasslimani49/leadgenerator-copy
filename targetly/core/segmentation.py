"""Segmentation commerciale — dérivation déterministe du palier « Qualification ».

Étape "Segmentation" du pipeline, en aval du BPS et du ranking. Module pur :
aucun effet de bord, aucun appel réseau. Il traduit un BPS [0, 100] en un
libellé commercial unique, exactement parmi les 4 valeurs du select Notion.

La qualification = niveau de PRIORITÉ COMMERCIALE issu du BPS (à ne pas
confondre avec le « Segment » métier du CRM, qui reste indépendant).
"""

# Bandes de qualification (bornes hautes incluses, décroissantes).
# 90-100 = Exceptionnel ; 80-89 = Prioritaire ; 75-79 = Opportunité ; <75 = Ignorer.
# Le plancher d'Opportunité a été RELEVÉ 70 -> 75 : couplé au scoring V3 (plus
# sélectif), il resserre l'entonnoir d'export (cf. EXPORT_MIN_BPS) pour ne laisser
# descendre dans Notion que les meilleurs profils. Libellés Notion inchangés.
QUALIFICATION_BANDS = (
    (90, "Exceptionnel"),
    (80, "Prioritaire"),
    (75, "Opportunité"),
)
QUALIFICATION_DEFAULT = "Ignorer"

# Libellés autorisés (= valeurs exactes du select « Qualification » dans Notion).
QUALIFICATION_VALUES = ("Exceptionnel", "Prioritaire", "Opportunité", "Ignorer")


def qualification(bps) -> str:
    """Palier commercial déterministe depuis le BPS.

    Tolère une entrée non numérique (None, "") -> "Ignorer" : un BPS absent
    ne doit jamais être promu en priorité commerciale.
    """
    try:
        value = int(bps)
    except (TypeError, ValueError):
        return QUALIFICATION_DEFAULT
    for threshold, label in QUALIFICATION_BANDS:
        if value >= threshold:
            return label
    return QUALIFICATION_DEFAULT


# Seuil unique d'export CRM = plancher de la bande « Opportunité », DÉRIVÉ des
# bandes (pas un littéral) : une seule source de vérité pour le seuil commercial,
# impossible à désynchroniser des paliers de qualification.
# Conséquence : un prospect est exporté SSI sa qualification != "Ignorer".
EXPORT_MIN_BPS = QUALIFICATION_BANDS[-1][0]


def should_export(bps) -> bool:
    """Décide si un prospect doit être poussé vers le CRM Notion.

    Critère unique et centralisé : BPS >= EXPORT_MIN_BPS. Une entrée non
    numérique (None, "") -> False : un BPS absent n'est jamais exporté par
    défaut (donnée absente ≠ qualifiée).
    """
    try:
        value = int(bps)
    except (TypeError, ValueError):
        return False
    return value >= EXPORT_MIN_BPS
