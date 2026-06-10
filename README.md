# Targetly (ex « Générateur de leads Vitryne »)

Moteur de prospection B2B locale : Google Places → scrape → extraction de
faits (Claude) → filtres → friction → BPS → Notion/CSV. Interface web temps
réel (SSE) + CLI opérateur. En cours de transformation en SaaS multi-tenant
(ADR-007) : voir `CLAUDE.md` et `projet-claude/06-backlog/now.md`.

Le **BPS /100** est le seul score d'achat : déterministe, explicable,
configurable. Claude est un **extracteur de preuves** (faits observables),
jamais un juge commercial : il n'émet aucune note de pertinence et le gate
d'export ne dépend que du BPS.

## Structure

```
targetly/
├── core/          # scoring pur, sans I/O réseau (bps, model, filters, friction,
│                  # ranking, segmentation, franchise, messaging, calibration, scorelog, booking)
├── pipeline/      # I/O d'enrichissement (discovery, scraper, cache)
├── platform/      # api (FastAPI/SSE), services (monitoring, csv_export,
│                  # campaign_history), integrations (notion_sync, feedback)
└── cli/           # leads.py (CLI opérateur)
web/templates/     # interface web
tests/             # runners autonomes (stdlib)
website/           # site vitrine statique (autonome)
app.py             # shim : lance l'interface web
targetly_leads.py  # shim : CLI opérateur
```

## Installation

```bash
pip install -r requirements.txt
```

Renseigner `.env` à la racine (jamais commité) :

```
GOOGLE_API_KEY=...
NOTION_TOKEN=...
ANTHROPIC_API_KEY=...
```

## Lancer l'interface

```bash
python3 app.py
```

Puis ouvrir **http://127.0.0.1:8000** dans le navigateur.

> N'ouvre **pas** le fichier `web/templates/index.html` directement
> (double-clic / `file://`) : le bouton « Lancer » a besoin du serveur pour
> appeler `/stream`. (Le site vitrine `website/index.html`, lui, s'ouvre
> directement.)

Saisir un ou plusieurs métiers et une ou plusieurs zones séparés par des
virgules, puis cliquer « Lancer » : le pipeline s'exécute et les prospects
qualifiés (BPS ≥ 75, seuil centralisé dans `segmentation.should_export`)
sont créés/mis à jour dans Notion automatiquement. Le bouton affiche une
erreur explicite si le serveur est injoignable ou si une clé `.env` manque.

Une saisie simple reste valide, par exemple `coiffeur` + `Lyon`. Les synonymes
et quartiers ne sont jamais générés automatiquement : l'opérateur fournit les
variantes.

Port configurable : `PORT=8001 python3 app.py`.

## Mode ligne de commande

```bash
python3 targetly_leads.py "kinésithérapeute" "Lyon"
```

### Export CSV (V1)

```bash
python3 targetly_leads.py "coiffeur" "Lyon" --csv            # targetly_leads_export.csv
python3 targetly_leads.py "coiffeur" "Lyon" --csv=mon_export.csv
python3 targetly_leads.py "coiffeur" "Lyon" --csv --dry-run  # compte sans rien écrire
```

Mêmes règles que Notion : seuls les leads au-dessus du seuil
`segmentation.should_export` sortent (module `csv_export.py`, UTF-8 BOM pour
Excel, sortie déterministe). Ce n'est pas un connecteur CRM : HubSpot/Salesforce
restent exclus en V1 (ADR-005).

### Historique des campagnes (V1)

```bash
python3 targetly_leads.py --history
```

Lecture seule du journal local `.vitryne_runs.jsonl` (écrit à chaque run réel) :
derniers runs, agrégats par métier × zone, évolution entre deux passages.
Aucun réseau, aucune clé requise (module `campaign_history.py`, ADR-006).

> Note : les fichiers de données locaux gardent leurs noms historiques
> (`.vitryne_cache.json`, `.vitryne_runs.jsonl`, `.vitryne_scores.jsonl`)
> jusqu'à la migration en base (chantier C2) — continuité des données.

## Tests

Runners autonomes (stdlib, sans pytest) :

```bash
for t in tests/test_*.py; do python3 "$t"; done
```

## Boucle conversion, calibration, messaging (modules purs)

Trois modules déterministes, sans réseau dans la logique cœur, **opt-in** :

- `platform/integrations/feedback.py` — relit le **Statut** Notion (lecture
  seule, jamais d'écriture des champs manuels) et le mappe en issue
  (`won`/`lost`/`meeting`/…). I/O injectable (`post=`) pour des tests sans réseau.
- `core/calibration.py` — propose des `BPS_WEIGHTS` recalés sur les issues
  réelles (gagné vs perdu). **Ne modifie jamais** les poids automatiquement :
  proposition + rapport lisible, désactivée tant que les données sont insuffisantes.
- `core/messaging.py` — `message_angle(bps)` donne l'angle d'approche par bande
  de qualification ; `response_rates(outcomes)` mesure le taux de réponse par
  bande (donnée absente ≠ taux 0).

Tests dédiés : `tests/test_feedback.py`, `tests/test_calibration.py`,
`tests/test_messaging.py`.
