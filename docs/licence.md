# Licence : Imperial Community License (ICL) 1.0

Texte complet : [`LICENSE`](../LICENSE) (le français fait foi, traduction anglaise incluse). Attribution : [`NOTICE`](../NOTICE). Site officiel : <https://imperial-corporation.pages.dev/icc>. Dépôt d'origine : <https://github.com/Imperial-Corporation/Imperial-CodeCenter>.

> Ce n'est **pas** une licence « Open Source » au sens de l'OSI (elle interdit l'usage payant). C'est une licence **libre et gratuite** : tout reste public, modifiable et gratuit. Elle est personnalisée : fais-la relire par un juriste avant tout usage à enjeu, et ne la mélange pas avec du code sous des licences incompatibles (GPL, MIT… dont les conditions diffèrent).

## En bref

| Tu peux | Tu dois | Tu ne peux pas |
|---|---|---|
| Utiliser, étudier, copier | Garder `LICENSE` et `NOTICE`, et la mention d'origine | L'utiliser dans un projet, programme ou service **payant** |
| Modifier, forker | Écrire visiblement « Basé sur Imperial CodeCenter, créé par Imperial » + le lien du site (README, docs, pied de page de l'interface, `--version`, description du paquet) | Facturer l'accès, l'hébergement, le support, l'installation ou des options |
| Redistribuer, héberger un service gratuit | Publier le **code source complet** de tout programme qui utilise le logiciel, sous la même licence, dès qu'il est diffusé (y compris comme service en ligne) | Garder privé, fermé, obfusqué ou chiffré un code qui l'utilise, une fois diffusé |
| Faire des dons volontaires | Indiquer que ton œuvre est modifiée, par qui, et ce qui a changé | Retirer ou cacher les crédits, ou laisser croire qu'Imperial approuve ton fork |
| Usage privé sans diffusion | Donner un nom distinct à un fork | Changer de licence, double licence, ajouter des restrictions (DRM, verrous) |

## Cas particulier : l'équipe Imperial (article 5.8)

Le **créateur** du logiciel et les **membres de l'équipe Imperial Corporation** ne sont pas tenus par les obligations de code ouvert, de gratuité et de non-commercialisation (articles 4 et 5, et interdiction de double licence de l'article 13.3) pour leurs propres usages et distributions. L'exception est **personnelle** : elle ne s'étend ni à leurs clients, partenaires ou sous-traitants, ni à toute autre personne, et ne couvre pas les contributions de tiers au-delà des droits concédés. Quiconque reçoit une copie de leur part reste soumis à la licence, sauf licence écrite distincte. L'attribution (article 3) reste due.

## Cas fréquents

- **Je l'utilise seul, chez moi** : autorisé, rien à publier tant que tu ne le diffuses à personne.
- **Je l'installe pour mon association / mon équipe, gratuitement** : autorisé si rien n'est facturé, si le code qui l'utilise est publié et si les mentions sont visibles.
- **Je l'ajoute à mon appli** : autorisé seulement si ton appli est entièrement publique (code source complet), gratuite et sous la même licence.
- **Je veux l'utiliser dans un produit ou un SaaS payant, même avec une version gratuite à côté** : interdit.
- **J'héberge une instance publique gratuite** : autorisé ; le lien vers le code source doit être visible dans l'interface.
- **Je vends l'installation ou le support** : interdit.
- **J'utilise un fournisseur d'IA payant à l'usage (OpenAI, Claude…)** : permis ; ses tarifs ne rendent pas le logiciel payant, tant que tu ne revends pas l'accès.
- **Je collecte du code avec le crawler** : le code collecté garde sa licence d'origine (le crawler ne prend que des licences permissives) ; respecte ses mentions.

## Dans le projet

- Chaque fichier source porte un en-tête d'origine et de licence (`scripts/add_license_headers.py`).
- `GET /api/v1/about` expose nom, version, licence, organisation et site (aussi dans `/docs`).
- Le pied de page de l'interface affiche l'attribution obligatoire et les liens vers `LICENSE` et `NOTICE`.
- Métadonnées de paquets : `LicenseRef-Imperial-Community-1.0`.
