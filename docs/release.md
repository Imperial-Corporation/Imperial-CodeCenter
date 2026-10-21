# Publier une version (`tag.sh`)

```bash
python3 scripts/version.py set 1.0.5        # met à jour tous les fichiers de version
# rédige le CHANGELOG (« ## 1.0.5 ») et release-notes/1.0.5.md (modèle : release-notes/TEMPLATE.md)
git commit -am "Version 1.0.5"
./scripts/tag.sh 1.0.5                      # crée le tag v1.0.5 et le pousse
```

`tag.sh` lit la version dans le fichier **`VERSION`** ; l'argument est facultatif et sert de garde-fou (s'il diffère de `VERSION`, le script refuse). Il vérifie : versions cohérentes dans tous les fichiers, notes de version présentes, arbre Git propre, branche (pas de HEAD détaché), tag inexistant en local et sur le dépôt distant. Puis il lance les tests API (si `uv` est installé), affiche les notes, demande confirmation, crée un tag annoté (message = notes) et pousse la branche puis le tag.

Options : `--dry-run` (n'agit pas), `--yes`, `--no-push`, `--skip-tests`, `--remote NOM`.

## Ce que fait GitHub ensuite (`.github/workflows/release.yml`)

1. **Vérifier** : tag = `VERSION` = tous les fichiers ; notes disponibles.
2. **Tests** : API, crawler, moteur Rust.
3. **Compiler** le moteur (wheels Python) :
   - Linux **amd64** et **arm64** (serveurs, Raspberry Pi, VPS ARM), manylinux ;
   - macOS (universal2 : Intel + Apple Silicon) et Windows x64 — *au mieux* : un échec n'empêche pas la release ;
   - sdist, archive des sources, `icc.pyz` (CLI autonome).
4. **Image Docker** amd64 + arm64 publiée sur `ghcr.io/imperial-corporation/imperial-codecenter` (onglet *Packages* du dépôt) avec les tags `X.Y.Z`, `X.Y`, `X` et `latest` ; **scan Trivy** (la publication échoue sur une faille CRITIQUE corrigeable), **signature cosign** sans clé, **attestation de provenance**, **SBOM** SPDX.
5. **Release GitHub créée automatiquement** : texte = `release-notes/X.Y.Z.md` (sinon la section du CHANGELOG), fichiers de compilation, SBOM et `SHA256SUMS`.

Une version ne s'écrase pas : pour refaire un tag, supprime-le d'abord (`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`) et la release GitHub correspondante.

## Réglages GitHub (une fois)

- *Settings → Actions → General → Workflow permissions* : **Read and write permissions**.
- Après la première publication : *Packages → imperial-codecenter → Package settings → Change visibility → Public* (et lie le paquet au dépôt) pour que `docker pull` fonctionne sans connexion.
- *Settings → Code security* : active *Code scanning* pour voir les résultats de CodeQL (`codeql.yml`) et Dependabot.

## Vérifier une image

```bash
cosign verify ghcr.io/imperial-corporation/imperial-codecenter:1.0.4 \
  --certificate-identity-regexp 'https://github.com/Imperial-Corporation/Imperial-CodeCenter/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://ghcr.io/imperial-corporation/imperial-codecenter:1.0.4 -R Imperial-Corporation/Imperial-CodeCenter
```
