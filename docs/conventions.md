# Conventions d'écriture d'un connecteur — le pourquoi

`CLAUDE.md` porte les règles en une ligne chacune. Cette page porte ce qui les a produites : on la
lit quand une règle paraît arbitraire, ou quand on s'apprête à faire l'exception.

## « Client-sensible » se lit sur le PROPRIÉTAIRE de l'accès

Un connecteur client-sensible ne vit jamais ici (repo public) : package privé + bridge (ADR 0003).
Mais **client-sensible veut dire « le back-office PROPRE d'un client »** — son outil interne chez
lui, son infra, ses accès.

Un **produit commercial utilisé par des milliers d'entreprises** n'en est pas un, même si son API
est fermée et qu'on a dû l'établir nous-mêmes : Planity, Pennylane, Zoho vivent ici, comme les
autres. La règle se lit sur le propriétaire de l'accès, jamais sur la difficulté d'y accéder —
trois lecteurs l'ont appliquée à tort à un SaaS et ont failli en sortir un connecteur parfaitement
ordinaire.

## Un secret ne part jamais en `params=`

En query string, un secret entre dans l'URL, donc dans le message de toute exception `requests` —
remonté à l'agent, journalisé, envoyé en breadcrumb Sentry — et dans les access logs du serveur
distant. Toujours **`data=`** (corps, RFC 6749 §2.3.1 pour OAuth), et pas de `raise_for_status()`
sur un endpoint token (son message porte l'URL).

Fuite vécue (#284). Garde-fou AST dans **oto-backend** (test « no secrets in query string »).

## L'auth d'une famille de connecteurs est un module partagé

Ex. `oto/tools/zoho/auth.py` — refresh OAuth + cache, source unique CRM / Desk / Analytics. Tant que
les trois dupliquaient ce bloc, un correctif n'en couvrait qu'un tiers : le cache de token (#233)
n'avait atterri que sur Analytics.

**Cache de token = process-wide, keyé par credential** (hash des secrets, jamais un secret en clair
comme clé) : le serveur construit un client **par appel MCP**, donc un cache porté par l'instance ne
sert jamais → un refresh par appel → rate-limit du provider (Zoho : tous les appels en 400 pendant
~5 min).

## Un refus d'un client ne prescrit jamais un outil MCP

La lib ne connaît pas le jeu d'outils servi à l'appelant (une CLI, un endpoint publié qui sert une
liste à l'inclusion…). Un message dit le FAIT (« la source est close à l'extraction ») et au plus
une condition (« si tu as un compte connecté, c'est par lui ») — jamais un nom d'outil ni une
famille `xxx_*`.

Vécu (oto-backend#632) : `SerperClient._NEVER_SCRAPABLE` nommait `unipile_*`, que l'appelant n'avait
pas — et qui n'existe même plus sous ce nom (`linkedin_unipile_*` depuis l'ADR 0010).
Garde : `tests/test_serper_scrape_guard.py`.

## Aucune coordonnée d'un tiers en dur — même publique

Les trois constantes de Planity (clé d'API Firebase, App ID, racine des lambdas) ont vécu dans
`oto/tools/planity/config.py` jusqu'à ce que GitHub les signale sur le dépôt public. Elles sont
publiques par conception — tout navigateur qui ouvre `pro.planity.com` les reçoit — donc les retirer
n'était pas un geste de sécurité, c'en était un de **généricité** : un client publié ici décrit un
PROTOCOLE, il ne se présente pas comme l'intégration officielle d'une entreprise dont il embarque
les coordonnées.

Elles se passent désormais par `PlanityEndpoints`, **sans valeur par défaut** — un défaut les aurait
remises ici sous un autre nom, et personne n'aurait vu la différence. Cliquet :
`tests/test_planity_client.py::test_aucune_valeur_de_planity_ne_subsiste_dans_le_paquet`, qui refuse
dans le paquet les trois empreintes de ces constantes — il les porte, on ne les recopie pas ailleurs.

⚠️ La règle vaut pour le prochain connecteur du même genre, pas seulement pour celui-ci.

## Nommer ce qu'on appelle est le métier d'un client ; raconter comment on l'a trouvé ne l'est pas

Hôtes, fonction de shard, endpoints, conventions d'appel : c'est du CODE, un client ne peut pas
appeler sans nommer, et ça reste ici comme pour n'importe quel connecteur.

Ce qui n'a pas sa place dans un dépôt publié — ni en README, ni en docstring, ni en commentaire —
tient en deux choses : le **récit de la reconstitution** (« lu dans le bundle », « capturé dans le
trafic », « pas encore résolu, à retrouver ») et le **diagnostic sur le tiers** (ce que son produit
vérifie ou pas, ce qu'il répond quand on se trompe). Ni l'un ni l'autre ne sert un lecteur du code,
et les deux nous engagent.

Ce qui reste est ce qui **justifie une décision d'implémentation et ne se lit pas dans le code**,
écrit à l'endroit qu'il justifie. ⚠️ Ça se vérifie à la relecture d'un fichier ENTIER, jamais ligne
à ligne : chaque phrase se défend seule, et c'est leur somme qui redonne le récit.

## Synchrone par défaut ; un extra se retraduit à l'origine

**Un client est SYNCHRONE, sauf quand l'amont ne le permet pas.** L'exception est `planity`, dont le
transport n'a pas d'équivalent synchrone : tout le package est `async`, d'où l'extra `planity`
(`httpx` + `websockets`) — deux libs pour un seul connecteur, ce n'est pas au socle. Ce n'est pas un
précédent à imiter, et le module le dit à l'endroit où quelqu'un aurait envie de « simplifier ».

⚠️ **Un extra manquant se retraduit À L'ORIGINE, jamais chez le consommateur.**
`oto/tools/planity/__init__.py` rattrape l'`ImportError` de `httpx`/`websockets` et rend « installe
`oto-core[planity]` » — parce que « No module named 'httpx' » est vrai, inutile, et devient chez
oto-backend une ligne de journal sur laquelle on cherche un bug d'import. Les consommateurs sont
plusieurs ; une règle posée chez l'un ne protège pas les autres. Elle ne s'applique QU'aux modules
de l'extra : un `ImportError` interne remonte tel quel.

## Découper un gros connecteur sans bouger son chemin d'import

Fichier de code < 500 lignes. Le point d'entrée reste `<svc>/client.py` (ou `<svc>/lib/<svc>_client.py`
côté google) : il porte la construction et le transport, et **compose des mixins par famille
d'appels** rangés dans `<svc>/_api/*.py` (un module = un domaine de l'API amont). Les constantes,
les types d'erreur et le parsing lourd sortent en modules frères (`const.py`, `errors.py`, `feed.py`),
et `client.py` les **réexporte** via `__all__`.

Pourquoi ce soin : le backend et oto-cli épinglent oto-core **par tag** — un symbole qui déménage ne
casse pas ici, il casse **au bump du pin**, ailleurs, plus tard. Fait sur unipile (1 702 L → 13
modules) et google/slides (1 516 L → 9 modules) ; le contrat est verrouillé par
`tests/test_unipile_surface_frozen.py` et `tests/test_slides_surface_frozen.py`, qui figent membres
et signatures et refusent tout module ≥ 500 lignes dans ces deux packages.

## La CI doit installer tout extra dont un TEST importe la dépendance

Elle installait `-e ".[anonymize]"` seul : `tests/test_gmail_headers_and_draft.py` importe le client
Gmail, donc `google-api-python-client` (extra `google`) → `ModuleNotFoundError` **à la collecte**,
pytest s'arrête avant le premier test et le job échoue sans rien avoir vérifié. `main` est restée
rouge six jours, et **aucune PR ne pouvait devenir verte** — la garde version-skew du backend
renvoyait alors des PR saines en échec.

Un extra ajouté se répercute dans `.github/workflows/ci.yml`.
