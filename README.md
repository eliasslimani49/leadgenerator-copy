# Générateur de leads Vitryne

Pipeline de qualification : Google Places → scrape → extraction de faits (Claude)
→ filtres → friction → BPS → Notion. Interface web temps réel (SSE).

Le **BPS /100** est le seul score d'achat : déterministe, explicable, configurable.
Claude est un **extracteur de preuves** (faits observables), jamais un juge commercial :
il n'émet aucune note de pertinence et le gate d'export ne dépend que du BPS.

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

> N'ouvre **pas** le fichier `templates/index.html` directement (double-clic /
> `file://`) : le bouton « Lancer » a besoin du serveur pour appeler `/stream`.

Saisir un ou plusieurs métiers et une ou plusieurs zones séparés par des virgules,
puis cliquer « Lancer » : le pipeline s'exécute et les
prospects qualifiés (BPS ≥ 75, seuil centralisé dans `segmentation.should_export`)
sont créés/mis à jour dans Notion automatiquement. Le bouton affiche désormais une
erreur explicite si le serveur est injoignable ou si une clé `.env` manque.

Une saisie simple reste valide, par exemple `coiffeur` + `Lyon`. Les synonymes et
quartiers ne sont jamais générés automatiquement : l'opérateur fournit les variantes.

Port configurable : `PORT=8001 python3 app.py`.

## Mode ligne de commande

```bash
python3 vitryne_leads.py "kinésithérapeute" "Lyon"
```

## Tests

Runners autonomes (stdlib, sans pytest) :

```bash
for t in test_*.py; do python3 "$t"; done
```

## Boucle conversion, calibration, messaging (modules purs)

Trois modules déterministes, sans réseau dans la logique cœur, **opt-in** :

- `feedback.py` — relit le **Statut** Notion (lecture seule, jamais d'écriture des
  champs manuels) et le mappe en issue (`won`/`lost`/`meeting`/…). I/O injectable
  (`post=`) pour des tests sans réseau.
- `calibration.py` — propose des `BPS_WEIGHTS` recalés sur les issues réelles
  (gagné vs perdu). **Ne modifie jamais** les poids automatiquement : proposition +
  rapport lisible, désactivée tant que les données sont insuffisantes.
- `messaging.py` — `message_angle(bps)` donne l'angle d'approche par bande de
  qualification ; `response_rates(outcomes)` mesure le taux de réponse par bande
  (donnée absente ≠ taux 0).

Tests dédiés : `test_feedback.py`, `test_calibration.py`, `test_messaging.py`.
