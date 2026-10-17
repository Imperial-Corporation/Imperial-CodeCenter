# Intelligence artificielle

![Configuration](images/configuration-omniroute.png)

## Fournisseurs

Panneau à engrenage, 18 fournisseurs : locaux — Ollama, OmniRoute, LM Studio, llama.cpp / vLLM ; distants — Claude, OpenAI, Groq, Gemini, OpenRouter, Mistral, DeepSeek, xAI, Together AI, Fireworks AI, Cerebras, NVIDIA NIM, Cohere ; plus « compatible OpenAI » libre (n'importe quelle URL `/v1`). Tous sauf Claude, Gemini et Ollama utilisent le protocole OpenAI. Configuration **globale** (un fournisseur et un modèle actifs pour tous). Les clés sont chiffrées et jamais renvoyées au navigateur. Voir le `README.md` racine pour les particularités de chaque protocole.

### OmniRoute : « Réponse du fournisseur illisible (JSON attendu) »

![Erreur d'origine](images/erreur-json-illisible-avant-correctif.png)

Cause : OmniRoute (passerelle compatible OpenAI) peut répondre en flux SSE (`data: {...}`), ou avec une page HTML (erreur de la passerelle, mauvaise URL), au lieu d'un JSON simple. Le connecteur gère maintenant les flux SSE et signale clairement une réponse HTML (URL à vérifier, souvent `http://127.0.0.1:20128/v1`, ou combo/modèle inexistant). Vérifications : `curl http://127.0.0.1:20128/v1/models`, le nom exact du modèle (`combo-ultime`), la clé si la passerelle en exige une, puis « Tester la connexion ».

## Profondeur : l'IA cherche selon ta question

`depth.py` évalue la demande :

| Mode | Quand | Effet |
|---|---|---|
| **Aperçu** | question générale (« comment faire une API en Python ? ») | explication simple et courte, peu de sources (2 fichiers joints au plus), 2 appels d'outils max. **Ne cherche pas** les sujets avancés (chiffrement, authentification…). |
| **Ciblé** | question précise (nom de fonction, erreur, langage) | comportement standard. |
| **Approfondi** | tu creuses (« détaille », « chiffrer l'API », case « Approfondir », bouton « Approfondir » sous la réponse) | davantage de sources (8 fichiers joints au plus) et d'outils, sources spécialisées. |

Le mode utilisé est affiché sous la réponse. On peut le forcer : champ `depth` (`auto`, `overview`, `focused`, `deep`).

## Sélection des sources (sobriété)

Seuls les fichiers réellement pertinents sont gardés : un résultat est conservé s'il atteint une fraction du meilleur score (aperçu 35 %, ciblé 25 %, approfondi 15 %) et joint au contexte au-dessus d'un second seuil (60 % / 45 % / 30 %), dans la limite de 2 / 5 / 8 fichiers. Le meilleur résultat est toujours gardé. Le prompt demande des réponses sobres : outils seulement si nécessaire, sources utiles seulement, code strictement nécessaire.

## Chatbox après la recherche

Une zone de saisie reste en bas de la discussion : pose une question de suite, demande un exemple ou un autre langage. L'IA garde l'historique (20 tours) et peut **toujours utiliser ses outils** (`search_code`, `read_file`…). Des pistes **« Aller plus loin »** (cliquables) sont proposées par l'IA.

Limites : les conversations sont **en mémoire du serveur** (2 h d'inactivité, 200 conversations max, 300 sources max par conversation) et disparaissent au redémarrage sauf si la discussion est **enregistrée** ([comptes.md](comptes.md)) ; une seule requête à la fois par conversation.

## Affichage Markdown

Titres, gras/italique, listes imbriquées, tableaux, citations, code **coloré** (bouton **Copier**, badge « Extrait de S1 » / « Adapté de S1 » / « Non vérifié »), liens `http(s)`/`mailto` seulement, références `[S1]` vers les sources. Le rendu est construit en DOM avec `textContent` : le HTML renvoyé par un modèle (`<script>`, `javascript:`) n'est jamais interprété.

## Statut « analysis_pending »

Un fichier peut apparaître en attente d'analyse après son indexation : c'est normal et sans effet sur la recherche.
