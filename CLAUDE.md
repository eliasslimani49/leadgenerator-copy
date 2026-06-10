# CLAUDE.md

## Role

Tu transformes Targetly (ex "generateur de leads Vitryne", outil interne
valide) en SaaS multi-tenant scalable, sans casser le moteur de qualification
existant. Priorite : la trajectoire MVP SaaS -> V1 publique, par increments
testables.

## Produit

Targetly livre des listes courtes de leads B2B locaux qualifies, expliques et
exportables, par campagne metier x zone.

Cibles : sales independants, consultants en prospection, agences commerciales
(multi-clients), equipes sales/growth.

Le moteur (discovery -> extraction de faits -> filtres -> friction -> BPS ->
qualification) est l'actif. Il devient le coeur d'une plateforme a comptes,
campagnes persistees et quotas. Ce n'est plus un outil a maintenir : c'est un
produit a construire autour d'un moteur stable.

## Invariants moteur (intouchables)

- BPS deterministe, explicable, configurable ; jamais de note IA.
- Claude = extracteur de faits observables ; ne juge pas, ne decide pas l'export.
- Filtres eliminatoires avant scoring.
- Absence de donnee != preuve negative.
- Aucun envoi automatique (email, SMS, message).
- Qualite > volume ; un lead exporte doit etre justifiable.
- Seuils et poids BPS : aucune modification sans donnees de calibration terrain.

## Architecture

targetly/            package Python principal
- core/              scoring pur, zero I/O reseau (bps, model, filters,
                     friction, ranking, segmentation, franchise, messaging,
                     calibration, scorelog)
- pipeline/          I/O d'enrichissement (discovery, scraper, cache, booking)
- platform/          couche SaaS
  - api/             app FastAPI (routes, SSE)
  - services/        orchestration campagnes, monitoring, csv_export,
                     campaign_history
  - integrations/    notion_sync, feedback
- cli/               leads.py (mode operateur)

web/templates/       UI Jinja + SSE
tests/               runners autonomes (stdlib, sans pytest)
website/             site vitrine statique (autonome)
app.py               shim racine : lance l'app FastAPI
targetly_leads.py    shim racine : CLI operateur

Stack actuelle : FastAPI + SSE, etat local en fichiers JSON/JSONL.
Stack cible (chantiers C2+) : Postgres + SQLAlchemy + Alembic, worker via
table runs (SELECT FOR UPDATE SKIP LOCKED, pas de Redis en V1), sessions
cookies signees + argon2, Stripe Checkout.

Regles plateforme (a appliquer des C2) :
- workspace_id obligatoire sur toute nouvelle table ; isolation dans la couche
  repository, jamais ailleurs.
- Cache de faits partage entre tenants (cle place_id) ; scoring rejoue par tenant.
- Cles Google/Anthropic = plateforme (cout trace par run) ; token Notion = par
  workspace, chiffre. Jamais de secret en clair en DB ou commite.
- Quotas en credits (1 credit = 1 etablissement analyse), ledger en DB.
- RGPD : les prospects independants sont des donnees personnelles. Retention
  configurable, purge a la suppression du workspace, pas de donnees sensibles.

Note continuite : les journaux locaux gardent leurs noms historiques
(.vitryne_cache.json, .vitryne_runs.jsonl, .vitryne_scores.jsonl) jusqu'a la
migration DB (C2). Ne pas les renommer.

## Regles de developpement

- Petits increments ; chaque lot laisse la suite au vert :
  for t in tests/test_*.py; do python3 "$t"; done
- core/ reste pur : aucun import reseau ou DB dedans.
- Tests : runners stdlib pour core/ et pipeline/ ; pytest autorise pour platform/.
- Schema : Alembic uniquement des que la DB existe, jamais de DDL manuel.
- Toute decision structurante = ADR courte dans projet-claude/05-decisions/.
- Pas de fonctionnalite hors du chemin acquisition -> qualification -> conversion.
- Reponds en francais. Charge uniquement les fichiers necessaires.
- Distingue MVP / V1 / futur. Signale les hypotheses, marque "a valider".

## Priorites (ordre)

Faits : C0 docs realignees, C1 repackaging + rebrand Targetly, CI, pyproject.

1. C2 persistance Postgres (remplace les JSONL ; multi-tenant des le schema ;
   schema cible dans projet-claude/03-architecture/saas-data-model.md).
2. C3 execution asynchrone des campagnes (worker + SSE depuis la DB).
3. C4 auth + workspaces + credits.
4. C5 UI SaaS minimale (campagnes, leads + preuves, export CSV, historique).
5. C6 beta privee (pilotes), puis V1 : espaces clients, dashboard, Notion
   opt-in, Stripe self-serve, pack RGPD.

Statut courant et detail : projet-claude/06-backlog/now.md.

## Garde-fous

- Un chantier a la fois ; pas de refonte du moteur ou de l'UI "au passage".
- Pas de Redis, microservices, SPA ou ML tant que la solution simple suffit.
- Connecteurs CRM : V2 (ADR-005). Emailing : jamais.
- La boucle terrain (outcomes -> calibration) continue en parallele : c'est le
  differenciant produit.
- Arbitrage flou : choisir l'option la plus simple a operer seul.

## Memoire projet

- Etat courant : projet-claude/06-backlog/now.md
- Decisions : projet-claude/05-decisions/decisions-index.md
- Vision : projet-claude/01-product/product-vision.md
- Architecture : projet-claude/03-architecture/architecture-overview.md
  et saas-data-model.md
- Site vitrine : website/ (statique, autonome)

projet-claude/ et .env restent non trackes (locaux au repo principal).
CLAUDE.md est tracke depuis la transformation SaaS.
