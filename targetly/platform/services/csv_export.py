"""Export CSV V1 des leads qualifiés — sortie locale en aval du pipeline.

Étape optionnelle après scoring (cf. specs-export-csv-v1.md, ADR-005) : écrit
les SEULS prospects éligibles selon le seuil centralisé
segmentation.should_export — jamais de contournement du gate d'export.

Sortie déterministe et idempotente : colonnes contractuelles stables,
dédoublonnage par place_id (le meilleur BPS gagne), tri stable, réécriture
complète du fichier. Deux exports des mêmes données -> fichiers identiques.

Logique pure (lignes, filtrage, tri) séparée de l'I/O (export_csv). Aucun
réseau, aucun secret : testable hors ligne. Ce module n'est PAS un connecteur
CRM : HubSpot/Salesforce restent exclus tant que l'ADR-005 n'est pas révisée.
Un échec d'écriture lève OSError : erreur VISIBLE, jamais avalée (le CSV est
une sortie principale, contrairement au cache best-effort).
"""

import csv
from typing import NamedTuple

from targetly.core.friction import besoins_from_flags
from targetly.core.segmentation import qualification, should_export

# Chemin par défaut : fixe (pas d'horodatage) pour rester idempotent ;
# l'opérateur passe --csv=chemin pour conserver plusieurs exports.
DEFAULT_CSV_PATH = "targetly_leads_export.csv"

# Ordre contractuel des colonnes (cf. specs-export-csv-v1.md). Pas de colonnes
# de workflow commercial (statut, responsable…) : lead intelligence, pas CRM.
CSV_COLUMNS = (
    "place_id",
    "nom",
    "ville",
    "activites",
    "telephone",
    "email",
    "site_web",
    "instagram",
    "facebook",
    "bps",
    "qualification",
    "niveau_structuration",
    "friction_score",
    "friction_flags",
    "besoins",
    "booking",
    "notes",
)

# Séparateur de listes DANS une cellule : lisible en tableur, ne casse pas le CSV.
LIST_SEPARATOR = " | "


# --- Logique pure -------------------------------------------------------------


def _merged_besoins(prospect) -> list:
    """Irritants FACTUELS (friction) d'abord, puis besoins Claude, dédupliqués.

    Même règle que la propriété Notion « Besoin / Problème » (notion_sync) ;
    dupliquée ici pour ne pas importer notion_sync (qui tire `requests`) et
    garder ce module strictement hors ligne.
    """
    besoins = list(besoins_from_flags(prospect.friction_flags))
    for b in (prospect.besoins or []):
        if b not in besoins:
            besoins.append(b)
    return besoins


def _structuration(prospect) -> str:
    """Niveau de structuration (business_maturity x100), VIDE si inconnu.

    Parité Notion : une dimension absente reste une cellule vide, jamais un 0
    implicite (donnée absente ≠ score nul).
    """
    entry = (prospect.bps_breakdown or {}).get("business_maturity") or {}
    if entry.get("known") and entry.get("score") is not None:
        return str(round(entry["score"] * 100))
    return ""


def prospect_row(prospect) -> dict:
    """Ligne CSV d'un prospect (mapping pur, cf. table des specs)."""
    return {
        "place_id": prospect.place_id or "",
        "nom": prospect.nom or "",
        "ville": prospect.ville or "",
        "activites": prospect.activites or "",
        "telephone": prospect.telephone or "",
        "email": prospect.email or "",
        "site_web": prospect.site_web or "",
        "instagram": prospect.instagram or "",
        "facebook": prospect.facebook or "",
        "bps": int(prospect.bps),
        "qualification": qualification(prospect.bps),
        "niveau_structuration": _structuration(prospect),
        "friction_score": int(prospect.friction_score),
        "friction_flags": LIST_SEPARATOR.join(prospect.friction_flags or []),
        "besoins": LIST_SEPARATOR.join(_merged_besoins(prospect)),
        # Affichage : la prose factuelle prime, repli sur le token canonique
        # (même priorité que la cellule Notion « Booking software »).
        "booking": prospect.booking_details or prospect.booking_software or "",
        "notes": prospect.notes or "",
    }


def eligible_prospects(prospects) -> list:
    """Prospects exportables : gate should_export + dédoublonnage + tri stable.

    - Éligibilité : seuil unique segmentation.should_export (jamais contourné).
    - Dédoublonnage par place_id : le meilleur BPS gagne (premier vu à égalité) ;
      sans place_id, pas de clé -> conservé tel quel.
    - Tri déterministe : BPS décroissant, puis nom, puis place_id (idempotence).
    """
    kept = []
    by_place_id = {}
    for p in prospects or []:
        if not should_export(p.bps):
            continue
        key = p.place_id or ""
        if not key:
            kept.append(p)
            continue
        seen = by_place_id.get(key)
        if seen is None:
            by_place_id[key] = p
        elif int(p.bps) > int(seen.bps):
            by_place_id[key] = p
    kept.extend(by_place_id.values())
    return sorted(kept, key=lambda p: (-int(p.bps), (p.nom or "").lower(), p.place_id or ""))


class ExportResult(NamedTuple):
    analyzed: int    # prospects reçus (scorés par le run)
    eligible: int    # au-dessus du seuil should_export, après dédoublonnage
    exported: int    # lignes réellement écrites (0 en dry-run)
    path: str
    dry_run: bool


# --- I/O ----------------------------------------------------------------------


def export_csv(prospects, path=DEFAULT_CSV_PATH, *, dry_run=False) -> ExportResult:
    """Écrit le CSV des leads éligibles ; en dry-run, calcule sans rien écrire.

    Réécriture complète (pas d'append), en-tête toujours présent même sans
    lead éligible. UTF-8 avec BOM (utf-8-sig) pour ouverture directe Excel FR.
    Échec d'écriture -> OSError remontée (jamais d'échec silencieux).
    """
    analyzed = len(prospects or [])
    rows = [prospect_row(p) for p in eligible_prospects(prospects)]
    if dry_run:
        return ExportResult(analyzed, len(rows), 0, path, True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return ExportResult(analyzed, len(rows), len(rows), path, False)


def format_export_report(result: ExportResult) -> str:
    """Bilan d'export en une ligne (jamais de secret dans les logs)."""
    if result.dry_run:
        return (f"[dry-run] CSV simulé : {result.analyzed} analysés, "
                f"{result.eligible} éligibles, 0 écrit (aurait exporté "
                f"{result.eligible} -> {result.path}).")
    return (f"CSV : {result.analyzed} analysés, {result.eligible} éligibles, "
            f"{result.exported} exportés -> {result.path}")
