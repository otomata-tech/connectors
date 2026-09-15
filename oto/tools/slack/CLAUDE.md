# Connecteur Slack (`oto.tools.slack`)

Client Slack Web API multi-workspace. Source : `client.py` (`SlackClient`), texte prétraité dans
`text.py` (module frère, pur, sans I/O). Exposé en CLI (`oto slack …`) et en MCP (`slack_*`).

Gestion d'erreur : les rejets logiques de Slack (`{"ok": false, "error": "<code>"}` en HTTP 200) sont
traduits en erreur typée portant `.status` (4xx amont = input rejeté, 5xx = incident Slack) — cf.
`_SLACK_ERROR_STATUS`. Sur un `missing_scope`, `SlackError` porte aussi **`needed`/`provided`** :
Slack NOMME lui-même le droit qui manque, l'aval le relaie au lieu de le deviner.

## 1. Modèle multi-workspace & résolution de tokens

Un `SlackClient` cible **un workspace** (`workspace="<slug>"`, défaut `otomata`). Il résout ses tokens
depuis les secrets par **convention de nommage**, `<SLUG>` = `workspace.upper()` :

| | clé secret | usage |
|---|---|---|
| bot token (`xoxb-`) | `SLACK_<SLUG>_BOT_TOKEN` | lecture + post « au nom de l'app » |
| user token (`xoxp-`) | `SLACK_<SLUG>_USER_TOKEN` | post « au nom de l'utilisateur » (`as_user=True`) |

Pour le workspace par défaut (`otomata`), les clés **plates legacy** `SLACK_BOT_TOKEN` /
`SLACK_USER_TOKEN` sont acceptées en repli. Aucun token résolu ⇒
`No Slack token for workspace '<slug>'…` → poser la clé.

`post_message`/`update_message`/`open_dm`/`add_reaction` acceptent `as_user=True|False` ; si omis →
`default_as_user` du client (défaut `False` = bot). **Lecture** (channels, history, replies,
`channel_info`, find-user) et **`join_channel`** → le bot suffit.

**Ajouter un 2ᵉ workspace** = poser `SLACK_<NOUVEAU_SLUG>_BOT_TOKEN` (+ `_USER_TOKEN` si post-as-user
voulu), puis `SlackClient(workspace="<nouveau_slug>")`. Aucune autre config.

## 2. Les gotchas de l'API Slack

⚠️ **`history()` ne rend que le PREMIER NIVEAU.** Sur un message parent il annonce
`reply_count`/`reply_users`/`latest_reply` mais **jamais un corps de réponse** : les réponses d'un fil
s'obtiennent par **`replies()`** (`conversations.replies`). Le contrat sondé — le paramètre s'appelle
`ts`, le parent revient en `messages[0]` et se répète à chaque page, `limit` borne les réponses sans
compter le parent, `oldest`/`latest` sont exclusives — est écrit dans la docstring de la méthode et
vérifié par `tests/test_slack_thread_replies.py`. ⚠️ Appelé avec le `ts` d'une **réponse**, Slack rend
ce seul message en `ok:true` : un « fil vide » qui n'en est pas un, à détecter en aval
(`messages[0].thread_ts != .ts`).

⚠️ **`join_channel()` ne vaut que pour les canaux PUBLICS.** Un canal privé ne se rejoint par **aucune**
API Slack — il faut qu'un humain déjà membre y invite l'app. `channel_info()` existe pour trancher
public/privé **avant** de tenter quoi que ce soit ; il répond sur un canal public non rejoint, mais rend
`channel_not_found` sur un canal privé où l'app n'est pas — indiscernable d'un ID faux, donc l'aval doit
dire les deux.

⚠️ **`find_user_by_email` dépend de l'email RÉEL du compte Slack.** `users.lookupByEmail` veut l'adresse
avec laquelle la personne s'est inscrite sur Slack, pas forcément son email pro : une adresse pro
parfaitement valide rend `users_not_found`. Lookup KO ⇒ vérifier l'email d'inscription de la cible.

⚠️ **Pas de méthode `whoami`.** Pour savoir sur quel workspace et sous quelle identité on agit, il faut
taper l'API `auth.test` directement.

## 3. `post_message` — deux gardes sur le texte

Le texte passe par `text.py` AVANT de partir :

- **Échappement des faux emoji** : Slack lit tout `:jeton:` purement numérique comme un shortcode, même
  inconnu — une heure `"20:51:"` suivie d'un `:` de ponctuation se lit `:51:` et avale les chiffres.
  `escape_false_emoji_shortcodes` casse ces jetons avec une espace de largeur nulle (invisible), sauf
  les deux exceptions réelles du jeu par défaut (`:100:`, `:1234:`).
- **Split au-delà de 4 000 caractères** — limite **recommandée** par Slack : au-delà, Slack ne refuse
  rien, il **TRONQUE en silence** à 40 000, et un envoi unique ressort en plusieurs messages.
  `post_message` découpe lui-même (`chunk_text`, coupe sur un mot ou un retour à la ligne), poste chaque
  partie et rend **`ts_all`** (tous les `ts`, dans l'ordre) en plus de `ts` — toujours le PREMIER, l'ancre
  à réutiliser pour répondre dans le même fil. Sans `thread_ts` fourni, les parties suivantes threadent
  sous la première ; avec un `thread_ts` fourni, toutes y restent rattachées.

## 4. Onboarding d'un nouveau user

1. Créer une app sur https://api.slack.com/apps (ou installer une app partagée) sur le workspace cible.
2. **Scopes** (OAuth & Permissions) : lecture / post bot = `chat:write`, `channels:read`,
   `users:read.email`, `im:write` ; **rejoindre un canal public** = `channels:join` ; recherche =
   `search:read` (⚠️ scope **user token** uniquement) ; post « as user » = un user token `xoxp-` en plus.
   ⚠️ **Lire l'historique ET les fils exige un scope `<surface>:history` PAR surface** —
   `channels:history` (public), `groups:history` (privé), `im:history` (DM), `mpim:history` (DM de
   groupe) ; `conversations.replies` exige le même que `conversations.history`.
3. **Installer** l'app → récupérer le `Bot User OAuth Token` (`xoxb-`) et, si besoin, le `User OAuth
   Token` (`xoxp-`), puis les **poser dans le vault** per-user (`~/.otomata/secrets/`, lu par
   `oto.config`) sous `SLACK_<SLUG>_BOT_TOKEN` / `SLACK_<SLUG>_USER_TOKEN`.
