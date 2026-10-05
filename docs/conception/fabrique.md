# La fabrique

- **Statut** : proposé
- **Dernière révision** : 2026-10-05

## Résumé

La fabrique lit les [fichiers de description](format-de-description.md) et génère, pour chaque connecteur partagé, un client Python dans la lib et des fonctions TypeScript au contrat du paquet d'oto 2, livrées dans un paquet npm que le paquet déclare et inscrit. Elle vit dans ce dépôt, à côté des descriptions, écrite en Python. Elle traduit la table des erreurs en refus typés et génère les tests qui prouvent que les deux sorties servent le même contrat. Rien n'en est encore écrit.

## Contexte

- Oto 2 connaît deux sortes de connecteurs. Un connecteur propre à un hôte s'écrit dans l'hôte, à la main, au contrat de fonction du paquet. Un connecteur partagé a sa source ici et passe par la fabrique. Le contrat côté hôte (fonction du catalogue, comptes, secret fourni à l'appel, coffre) est décrit dans oto-pkg : docs/conception/connecteurs-et-comptes.md ; ce document ne le répète pas.
- Le langage servi dépend du consommateur, pas du connecteur : Python pour tout consommateur écrit en Python, TypeScript pour le paquet d'oto 2.
- Côté paquet, au relevé du 29/09 : les fonctions s'inscrivent par une liste statique d'imports, la source TypeScript est transpilée par l'hôte, les dépendances sont épinglées, `zod` est une dépendance paire.
- Côté lib : depuis la version 1.148.0, aucun client ne lit de secret ; il le reçoit en paramètre et l'exige par `require`, qui lève `MissingCredential`.
- Le backend d'oto 1 consomme la lib en Python et y garde ses outils écrits à la main, dont les neuf outils Sellsy.

## Objectifs et non-objectifs

- Une description, deux sorties qui servent le même contrat, prouvé par des tests.
- Une sortie TypeScript qu'un hôte d'oto 2 inscrit sans écrire de code.
- Le doublage à la main reste l'exception : un connecteur hors format, ou une entrée `handwritten`.
- Une description invalide ne génère rien.
- Hors objectif : générer quoi que ce soit pour le backend d'oto 1, qui garde ses connecteurs tels qu'ils sont.
- Hors objectif : convertir les connecteurs existants ([format](format-de-description.md)).
- Hors objectif : le contrat côté hôte, l'activation, la résolution du compte et le coffre (oto-pkg : docs/conception/connecteurs-et-comptes.md).

## Conception

### Ce qu'elle lit et vérifie

Chaque `connectors/<nom>/connector.yaml`, validé contre `connectors/connector.schema.json`, puis les deux règles que le schéma JSON ne sait pas dire : chaque argument d'`input` va à un seul endroit de `call` (chemin, query, corps ou en-tête), et chaque référence de `auth` nomme un champ de `credential`. Un argument sans place, ou une référence morte, fait refuser le fichier.

### Ce qu'elle génère

| Sortie | Contenu |
|---|---|
| Python, dans la lib | Une méthode par fonction sur le client du connecteur, au-dessus d'un runtime commun : transport, authentification par `auth.kind`, encodage des listes (`query_arrays`), pagination. |
| TypeScript, en paquet npm | Une fonction par entrée de `functions`, au contrat du paquet : nom, connecteur, classe, schéma d'entrée, exemples, refus, exécution, résumé de confirmation ; et la liste que le paquet inscrit à son catalogue. |
| Les deux | La table des erreurs et les `refusals` traduits en erreurs typées ; les tests de contrat. |

- La pagination ajoute `all_pages` et `max_pages` au schéma servi ; `max_pages` borne le parcours.
- La projection suit `exposure.mode` : en `per_action`, un outil `<namespace>_<name>` par fonction, schéma tel quel ; en `per_connector`, un outil qui reçoit `action` (l'énumération des noms) et un schéma en `oneOf` des entrées ; en `via_call`, rien n'est exposé et le verbe d'appel lit le registre.
- Tests de contrat : le schéma d'entrée servi est le même des deux côtés ; chaque exemple est joué contre le schéma et contre un serveur simulé.

### Ce qu'elle ne génère pas

- Le corps d'une entrée `handwritten` et l'authentification `oauth2_user`.
- Le runtime commun, écrit une fois par langage : cache de jeton (au niveau du processus, indexé par une empreinte du credential, jamais par le secret en clair), rejeu unique sur 401, limiteur de débit partagé.
- La fusion de plusieurs appels, les refus locaux avant l'appel, la construction d'un mail, l'identité opérée.
- Le texte d'un résultat, tant que la question de qui l'écrit reste ouverte.

### Le secret

Le code généré ne lit aucun secret. En Python, le constructeur du client reçoit les champs de `credential` et les exige par `require`. En TypeScript, le secret vient du contexte d'appel que l'hôte fournit, une fois le compte résolu (oto-pkg : docs/conception/connecteurs-et-comptes.md). Un appel sans secret échoue ; aucune clé par défaut ne le remplace.

### Le nom d'un connecteur

`connector.name` est la clé par laquelle un hôte référence le connecteur. Côté oto 2, le catalogue des connecteurs est une table et toute référence y est une clé étrangère, pour qu'un renommage ne fasse jamais disparaître des outils en silence (leçon d'oto 1, où une installation au nom inconnu était ignorée au chargement). Renommer un connecteur est donc une migration chez l'hôte, jamais une simple édition du fichier.

### Livraison

- La lib se publie sur PyPI, sur un tag posé à la main (`docs/release.md`).
- Le paquet npm des connecteurs partagés se publie de même, sur un tag posé à la main avec l'accord du mainteneur. Le paquet d'oto 2 le déclare en dépendance et inscrit ses fonctions ; la source de vérité reste ici.

## Décisions et alternatives écartées

- **Écrire chaque connecteur partagé deux fois à la main** : écarté le 29/09, les deux versions divergeraient. Il reste l'exception, pour un connecteur hors format.
- **Un service connecteurs séparé, en Python, sans état, le secret dans un en-tête** : abandonné le 29/09. Les connecteurs s'exécutent dans le paquet, chez l'hôte ; le détail et ses raisons sont dans oto-pkg : docs/conception/connecteurs-et-comptes.md.
- **La fabrique dans le paquet, en TypeScript** : écarté. Elle vit à côté des descriptions et de la lib qu'elle génère, pour qu'une seule source fasse foi.
- **Générer pour le backend d'oto 1** (un lecteur du format dans ce backend) : écarté le 29/09. Il vit avec ses connecteurs tels qu'ils sont, et ses outils Sellsy ne sont pas touchés.
- **Partir de zéro, sans la lib** : écarté le 30/09. La lib est gardée pour les connecteurs partagés, au secret fourni par le consommateur.

## Sécurité et confidentialité

- Le code généré ne lit aucun secret, ni dans l'environnement ni dans un fichier. Le Python généré vit sous `oto/` et tombe sous la garde `tests/test_no_secret_read_guard.py`.
- Un secret ne part jamais dans la query d'une URL : il finirait dans les messages d'erreur et les journaux. La fabrique le place en en-tête ou dans le corps de la requête de jeton, comme `auth` le décrit.
- Un refus généré dit le fait, au plus une condition, jamais le nom d'un outil (`docs/conventions.md`).
- Le dépôt et ses sorties sont publics : aucune description, aucun code généré ne nomme un client.

## Écart avec le code

- Aucune fabrique : ni générateur, ni runtime commun dans l'un ou l'autre langage, ni paquet npm de connecteurs, ni test de contrat entre les deux langages. Seuls le schéma, le fichier Sellsy et leur test de validation existent.
- Les deux règles hors schéma JSON ne sont vérifiées nulle part.
- Le client Python Sellsy est écrit à la main, sur des verbes génériques (`list_records`, `search_records`) ; il n'est pas généré.
- Côté paquet d'oto 2, au 05/10 (version 1.4.0), la sortie TypeScript n'a pas encore où se brancher : la fonction de connecteur n'est exportée que pour les sources du paquet, son origine porte encore le nom du service abandonné, le contexte d'appel ne porte pas de secret (oto-pkg : docs/conception/connecteurs-et-comptes.md, « Écart avec le code »).
- Trois formes diffèrent entre la description et le contrat du paquet : les refus sont une table structurée ici, une liste de chaînes là-bas ; un exemple porte un titre ici, pas là-bas ; le schéma d'entrée est un JSON Schema ici, un objet `zod` strict là-bas.

## Questions ouvertes

- Qui fait tourner la fabrique, et quand : à la main, en CI, au tag ?
- La sortie générée est-elle commitée, ou produite à la publication ?
- Le nom et la portée du paquet npm des connecteurs partagés.
- Qui écrit le texte d'un résultat, que le client Python rend en dictionnaire brut et que le paquet exige ?
- Le schéma servi au paquet : `zod` généré depuis le JSON Schema, ou un JSON Schema accepté par le paquet ?
- Les refus nommés côté paquet : des chaînes, ou la table structurée (`code`, `when`, `message`) ?
- Pendant la bascule d'oto 1 vers oto 2, un connecteur utile sans description passe-t-il par un doublage à la main ?

## Historique

- 2026-09-29 : une description par connecteur et une fabrique qui génère Python et TypeScript ; le doublage à la main reste l'exception — décidé par le mainteneur (source : séance du 29/09 ; conception connecteurs d'oto 2, oto-enterprise, archivé).
- 2026-09-29 : la fabrique vit dans ce dépôt, en Python ; elle génère la lib Python et publie la sortie TypeScript en paquet npm, que le paquet d'oto 2 déclare et inscrit ; le backend d'oto 1 est hors périmètre — décidé par le mainteneur (source : séance du 29/09, « Périmètre, sortie TypeScript, secrets »).
- 2026-09-29 : la lib ne lit plus aucun secret : résolution locale et ses quatre fournisseurs retirés, secret exigé par `require` ; publié en 1.148.0 — décidé par le mainteneur (source : séance du 29/09).
- 2026-09-30 : la lib est gardée pour les connecteurs partagés, au secret fourni par le consommateur — décidé par le mainteneur, accord du responsable du paquet à l'oral (source : point du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
