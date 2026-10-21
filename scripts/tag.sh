#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.
#
# Publie une version : crée le tag Git `vX.Y.Z` et le pousse. GitHub Actions fait le reste
# (tests, compilation amd64/arm64, image Docker, release avec ses notes).
#
#   ./scripts/tag.sh              # version lue dans le fichier VERSION
#   ./scripts/tag.sh 1.0.4        # idem, avec contrôle : doit être égale à VERSION
#   ./scripts/tag.sh 1.0.4 --dry-run   # affiche ce qui serait fait, ne change rien
#
# Options : --dry-run, --no-push (tag local seulement), --yes (sans confirmation),
#           --remote NOM (défaut : origin), --skip-tests.
#
# Notes de version : release-notes/X.Y.Z.md (modèle : release-notes/TEMPLATE.md), sinon la section
# « ## X.Y.Z » du CHANGELOG.md.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "Erreur : $*" >&2; exit 1; }
info() { echo "→ $*"; }

DRY=0 PUSH=1 YES=0 TESTS=1 REMOTE=origin ASKED=""
while (($#)); do
    case "$1" in
        --dry-run) DRY=1 ;;
        --no-push) PUSH=0 ;;
        --yes | -y) YES=1 ;;
        --skip-tests) TESTS=0 ;;
        --remote) shift; REMOTE="${1:-}"; [[ -n "$REMOTE" ]] || die "--remote attend un nom." ;;
        -h | --help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "option inconnue : $1" ;;
        *) [[ -z "$ASKED" ]] || die "une seule version attendue."; ASKED="${1#v}" ;;
    esac
    shift
done

command -v git >/dev/null 2>&1 || die "git est introuvable."
command -v python3 >/dev/null 2>&1 || die "python3 est introuvable."
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "ceci n'est pas un dépôt Git."

VERSION="$(tr -d '[:space:]' < VERSION)"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION invalide : « $VERSION »."
if [[ -n "$ASKED" && "$ASKED" != "$VERSION" ]]; then
    die "tu demandes $ASKED mais le fichier VERSION contient $VERSION.
   Aligne d'abord les fichiers :  python3 scripts/version.py set $ASKED   (puis complète CHANGELOG.md et release-notes/$ASKED.md)"
fi
TAG="v$VERSION"

info "Version : $VERSION (tag $TAG)"

# 1. Cohérence des versions + présence des notes de version.
python3 scripts/version.py check --tag "$TAG" --notes >/dev/null || {
    python3 scripts/version.py check --tag "$TAG" --notes || true
    die "versions incohérentes ou notes de version absentes (voir ci-dessus)."
}
info "Versions cohérentes, notes de version présentes."

# 2. Dépôt propre, branche suivie, tag inédit.
BRANCH="$(git symbolic-ref --short -q HEAD || true)"
[[ -n "$BRANCH" ]] || die "HEAD détaché : place-toi sur une branche."
if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    die "des modifications ne sont pas validées : fais un commit d'abord."
fi
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "le tag $TAG existe déjà en local (git tag -d $TAG pour le refaire)."
if ((PUSH)); then
    git remote get-url "$REMOTE" >/dev/null 2>&1 || die "le dépôt distant « $REMOTE » n'existe pas."
    if git ls-remote --exit-code --tags "$REMOTE" "refs/tags/$TAG" >/dev/null 2>&1; then
        die "le tag $TAG existe déjà sur $REMOTE."
    fi
fi

# 3. Tests rapides en local (la CI refait tout).
if ((TESTS)); then
    if command -v uv >/dev/null 2>&1 && ((DRY == 0)); then
        info "Tests API (uv)…"
        (cd api && uv run --frozen pytest -q -p no:cacheprovider) || die "les tests échouent : version non publiée."
    else
        info "Tests locaux ignorés (uv absent ou --dry-run) : la CI les exécutera."
    fi
fi

NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
python3 scripts/version.py notes "$VERSION" > "$NOTES_FILE"
echo
echo "──── Notes de la release $VERSION ────"
sed -n '1,25p' "$NOTES_FILE"
[[ "$(wc -l < "$NOTES_FILE")" -le 25 ]] || echo "[…]"
echo "──────────────────────────────────────"

if ((PUSH)); then
    info "Va créer le tag $TAG sur $(git rev-parse --short HEAD) et pousser « $BRANCH » + $TAG vers $REMOTE."
else
    info "Va créer le tag $TAG sur $(git rev-parse --short HEAD) (local, sans push)."
fi

if ((DRY)); then
    info "--dry-run : rien n'a été modifié."
    exit 0
fi

if ((YES == 0)); then
    read -r -p "Confirmer ? [o/N] " answer
    [[ "$answer" =~ ^[oOyY]$ ]] || die "annulé."
fi

git tag -a "$TAG" -F "$NOTES_FILE"
info "Tag $TAG créé."

if ((PUSH)); then
    # La branche d'abord (si elle a de l'avance), puis le tag : la CI part du tag.
    git push "$REMOTE" "$BRANCH"
    git push "$REMOTE" "$TAG"
    SLUG="$(git remote get-url "$REMOTE" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
    info "Tag poussé. Suis la publication : https://github.com/$SLUG/actions"
    info "La release apparaîtra sur https://github.com/$SLUG/releases/tag/$TAG"
else
    info "Pour publier : git push $REMOTE $BRANCH && git push $REMOTE $TAG"
fi
