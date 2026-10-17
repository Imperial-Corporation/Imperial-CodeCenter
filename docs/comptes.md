# Comptes et discussions enregistrées

Facultatif : on peut utiliser l'application sans compte. Un compte sert à **garder** ses discussions.

## Compte

- Création et connexion par **pseudo + mot de passe** (bouton en bas de la barre latérale gauche).
- Pseudo : 3 à 32 caractères (`A-Z a-z 0-9 _ . -`). Mot de passe : 8 à 128 caractères, différent du pseudo.
- Mot de passe stocké sous forme d'empreinte **scrypt** salée ; jamais en clair, jamais renvoyé.
- Session : cookie `icc_session`, `HttpOnly`, `SameSite=Lax`, `Secure` derrière HTTPS (ou si `CODECENTER_FORCE_HTTPS=1`). Le jeton n'est stocké en base que sous forme d'empreinte SHA-256 ; durée 30 jours. Le navigateur ne stocke rien d'autre (ni `localStorage`).
- Protection contre la force brute : 5 échecs par (pseudo, IP) et 30 par IP sur 15 minutes → `429` avec `Retry-After`.
- Dans la fenêtre de compte : changer le mot de passe, se déconnecter, supprimer le compte (efface aussi toutes ses discussions).
- `CODECENTER_REGISTRATION=closed` ferme les inscriptions (les comptes existants continuent de fonctionner).

Les données sont dans `<CODECENTER_DATA_DIR>/accounts.db` (SQLite, permissions 0600). Les routes d'administration (configuration IA, crawler) restent protégées par `CODECENTER_ADMIN_TOKEN`, pas par les comptes.

## Barre latérale

Un rail étroit à gauche se déplie quand la souris (ou le clavier) s'en approche ; sur téléphone, le bouton en haut à gauche l'ouvre. Elle contient : **Nouvelle discussion**, la liste des discussions enregistrées (ouvrir, renommer ✎, supprimer ✕) et le compte.

## Discussion temporaire ou définitive

1. Poser une question : l'écran d'accueil s'efface et laisse place à la discussion (question, réponse en carte, zone de saisie en bas).
2. La discussion est **temporaire** (en mémoire du serveur, 2 h, perdue au redémarrage).
3. **Enregistrer** (en haut) la rend **définitive** : elle est copiée dans le compte, et chaque question suivante s'y ajoute automatiquement.
4. Une discussion enregistrée se rouvre depuis la barre latérale, même après un redémarrage ; on peut la poursuivre (les sources et l'historique sont reconstruits).

Limites : 200 discussions par compte, 3 Mo par discussion.
