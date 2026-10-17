#!/usr/bin/env bash
# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

# Rend le dossier surveillé utilisable à la fois par le service ET par toi,
# sans chown/chmod généralisés et sans toucher aux propriétaires des fichiers.
#
# Pourquoi : le moteur d'indexation range les fichiers dans <catégorie>/<langage>/.
# Il doit donc pouvoir ÉCRIRE dans les dossiers (créer un sous-dossier, déplacer).
# Des fichiers déposés par root ou par un autre compte, dans des dossiers
# "root:root 775", sont lisibles mais non inscriptibles pour le service.
# Depuis la v1.0.0 l'indexation n'échoue plus dans ce cas (elle se fait sans
# rangement), mais ce script règle la cause :
#
#   1. groupe du service sur tout l'arbre (propriétaires inchangés) ;
#   2. dossiers en 2775 (setgid : les nouveaux fichiers héritent du groupe) ;
#   3. fichiers en 664 ;
#   4. ACL par défaut si `setfacl` existe (droits hérités quel que soit l'umask) ;
#   5. ton compte ajouté au groupe du service (reconnecte-toi ensuite).
#
# Usage : fix_permissions.sh [dossier_surveillé] [nom_du_service] [--user NOM] [--dry-run]
#   Défauts : dossier = DATA_DIR/codes de config/deploy.conf ; service = SERVICE_NAME.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=""
SERVICE=""
ADMIN_USER="${SUDO_USER:-${USER:-}}"
DRY=0

while (($#)); do
    case "$1" in
        --user) ADMIN_USER="${2:?--user attend un nom}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) awk 'NR>=5 && /^#/ {print; next} NR>5 {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "Option inconnue : $1" >&2; exit 2 ;;
        *) if [[ -z "$TARGET" ]]; then TARGET="$1"; elif [[ -z "$SERVICE" ]]; then SERVICE="$1"; fi; shift ;;
    esac
done

if [[ -z "$TARGET" || -z "$SERVICE" ]]; then
    # shellcheck source=/dev/null
    [[ -f "$ROOT/config/deploy.conf" ]] && source "$ROOT/config/deploy.conf"
    TARGET="${TARGET:-${DATA_DIR:-}/codes}"
    SERVICE="${SERVICE:-${SERVICE_NAME:-}}"
fi

die() { echo "Erreur : $*" >&2; exit 1; }
[[ -n "$TARGET" && "$TARGET" != "/codes" ]] || die "Dossier surveillé inconnu."
[[ "$TARGET" == /* ]] || die "Le dossier doit être un chemin absolu."
[[ "$TARGET" != "/" ]] || die "Refus de traiter la racine."
[[ "$SERVICE" =~ ^[a-zA-Z0-9_-]+$ ]] || die "Nom de service invalide."
getent group "$SERVICE" >/dev/null || die "Groupe '$SERVICE' introuvable (lance d'abord deploy.sh)."

SUDO=""
((EUID == 0)) || SUDO="sudo"
run() { if ((DRY)); then echo "[dry-run] $*"; else $SUDO "$@"; fi; }

# Le dossier parent (DATA_DIR) appartient au service en 750 : un compte normal
# ne peut pas le traverser, donc `test -d` sans sudo répondrait faussement « introuvable ».
$SUDO test -d "$TARGET" || die "Dossier introuvable : $TARGET"

echo "==> Dossier : $TARGET"
echo "==> Groupe du service : $SERVICE"

# 1-3. Groupe, setgid, modes — sans changer les propriétaires.
run find "$TARGET" ! -group "$SERVICE" -exec chgrp -h "$SERVICE" {} +
run find "$TARGET" -type d -exec chmod 2775 {} +
run find "$TARGET" -type f -exec chmod 664 {} +

# 4. ACL par défaut : le groupe du service garde ses droits sur les futurs fichiers.
if command -v setfacl >/dev/null 2>&1; then
    if run setfacl -R -m "g:${SERVICE}:rwX" "$TARGET" && run setfacl -R -d -m "g:${SERVICE}:rwX" "$TARGET"; then
        echo "==> ACL appliquées (droits hérités par les futurs fichiers)."
    else
        echo "Avertissement : ACL non appliquées (système de fichiers sans ACL ?). Le setgid reste actif." >&2
    fi
else
    echo "==> setfacl absent : seul le setgid est utilisé (installe le paquet 'acl' pour plus de robustesse)."
fi

# 5. Ton compte dans le groupe du service.
if [[ -n "$ADMIN_USER" && "$ADMIN_USER" != "root" ]] && id "$ADMIN_USER" >/dev/null 2>&1; then
    if id -nG "$ADMIN_USER" | tr ' ' '\n' | grep -qx "$SERVICE"; then
        echo "==> $ADMIN_USER est déjà dans le groupe $SERVICE."
    else
        run usermod -aG "$SERVICE" "$ADMIN_USER"
        echo "==> $ADMIN_USER ajouté au groupe $SERVICE : reconnecte-toi (ou 'newgrp $SERVICE') pour en profiter."
    fi
fi

# Vérification : le service peut-il vraiment écrire ?
if ((DRY == 0)) && command -v runuser >/dev/null 2>&1 && id "$SERVICE" >/dev/null 2>&1; then
    if $SUDO runuser -u "$SERVICE" -- test -w "$TARGET"; then
        echo "==> Vérification : le service peut écrire dans $TARGET."
    else
        echo "Avertissement : le service ne peut toujours pas écrire dans $TARGET (montage en lecture seule ?" \
             "ReadWritePaths= du service ?)." >&2
    fi
fi
echo "Permissions corrigées."
