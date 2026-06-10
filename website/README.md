# Site vitrine Vitryne (V1)

Landing page statique de présentation du SaaS de génération de leads qualifiés.
Aucun lien avec le moteur Python : ce dossier est autonome et ne touche à rien d'autre.

## Ouvrir le site en local

Double-cliquer sur `index.html` (ou glisser le fichier dans un navigateur).
Aucun serveur, aucune installation, aucune connexion réseau requis.

Optionnel, pour servir le site via HTTP :

```bash
cd website
python3 -m http.server 8080
# puis ouvrir http://localhost:8080
```

> Ne pas confondre avec `templates/index.html` à la racine du projet,
> qui est l'interface de l'application Python et nécessite `app.py`.

## Structure

```
website/
├── index.html              # Landing page unique (toutes les sections)
├── assets/
│   ├── css/styles.css      # Design system complet (responsive, sans dépendance)
│   ├── js/main.js          # JS léger : menu mobile, apparitions au scroll, année
│   └── img/favicon.svg     # Favicon (logo V)
└── README.md
```

## Choix techniques

- **Zéro dépendance externe** : polices système, icônes SVG inline, aucun CDN.
  Le site fonctionne hors ligne et en `file://`.
- **JS facultatif** : tout le contenu (y compris la FAQ, en `<details>` natifs)
  reste lisible si JavaScript est désactivé.
- **Responsive** : grilles fluides, menu mobile, points de rupture à 980 / 880 / 640 / 520 px.
- **Accessibilité** : lien d'évitement, navigation au clavier, `prefers-reduced-motion`
  respecté, contrastes AA.

## À personnaliser avant publication

1. **Adresse email** : les liens `mailto:` utilisent une adresse provisoire
   (`elias.bouron@essca.eu`). Rechercher `TODO` dans `index.html` et remplacer
   les 4 occurrences de l'adresse.
2. **Exemple du hero** : la fiche prospect « Studio Lumière » est illustrative ;
   adapter le métier/la zone si besoin (section `hero-visual` dans `index.html`).
3. **Couleurs / typo** : tout est centralisé dans les variables `:root`
   en tête de `assets/css/styles.css`.

## Publication future

Le dossier se déploie tel quel sur n'importe quel hébergement statique
(Netlify, GitHub Pages, OVH, etc.) : il suffit de publier le contenu de `website/`.
