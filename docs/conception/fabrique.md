# La fabrique

- **Statut** : proposé
- **Dernière révision** : 2026-10-06

## Résumé

La fabrique lit les [fichiers de description](format-de-description.md) et génère, pour chaque connecteur partagé, un client Python dans la lib et des fonctions TypeScript au contrat du paquet d'oto 2, livrées dans un paquet npm que le paquet déclare et inscrit. Elle vit dans ce dépôt, à côté des descriptions, écrite en Python. Elle traduit la table des erreurs en refus typés et génère les tests qui prouvent que les deux sorties servent le même contrat. Rien n'en est encore écrit.

## Contexte

- Oto 2 connaît deux sortes de connecteurs. Un connecteur propre à un hôte s'écrit dans l'hôte, à la main, au contrat de fonction du paquet. Un connecteur partagé a sa source ici et passe par la fabrique. Le contrat côté hôte (fonction du catalogue, comptes, secret fourni à l'appel, coffre) est décrit dans oto-pkg : docs/conception/connecteurs-et-comptes.md ; ce document ne le répète pas.
- Le langage servi dépend du consommateur, pas du connecteur : Python pour tout consommateur écrit en Python, TypeScript pour le paquet d'oto 2.
- Côté paquet, au relevé du 29/09 : les fonctions s'inscrivent par une liste statique d'imports, la source TypeScript est transpilée par l'hôte, les dépendances sont épinglées, `zod` est une dépendance paire.
- Côté lib : depuis la version 1.148.0, aucun client ne lit de secret ; il le reçoit en paramètre et l'exige par `require`, qui lève `MissingCredential`.
- Le backend d'oto 1 consomme la lib en Python et déclare à la main, pour chaque connecteur, ses outils (nom, description, schéma) et une logique propre (avertissements, bornes, messages d'erreur) : une deuxième version de ce que dit la description.

## Objectifs et non-objectifs

- Une description, deux sorties qui servent le même contrat, prouvé par des tests.
- Une sortie TypeScript qu'un hôte d'oto 2 inscrit sans écrire de code.
- Le doublage à la main reste l'exception : un connecteur hors format, ou une entrée `handwritten`.
- Une description invalide ne génère rien.
- Une seule version d'un connecteur pour oto 1 et oto 2 : le backend d'oto 1 bascule connecteur par connecteur sur la description (voir « Oto 1, connecteur par connecteur »).
- Hors objectif : basculer d'un coup les outils d'oto 1 ; un connecteur dont oto 2 n'a pas l'usage peut s'éteindre avec oto 1 sans jamais être décrit.
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

### Oto 1, connecteur par connecteur

Quand la fabrique sait générer un connecteur :
1. elle produit son client Python et ses fonctions TypeScript depuis la description ; le client Python écrit à la main est retiré ;
2. le backend d'oto 1 retire son fichier d'outils pour ce connecteur et l'enregistre par un lecteur générique de la description (écrit une fois), qui appelle le client généré ; sa logique propre passe dans la description ou dans le code écrit à la main que celle-ci référence, partagé avec oto 2 ;
3. les noms d'outils d'oto 1 de ce connecteur changent ce jour-là, avec une note de migration pour ses utilisateurs.

Le double ne reste qu'aux connecteurs pas encore basculés, et il s'éteint au premier des deux termes : tout basculé, ou oto 1 arrêté. Le lecteur exige que les descriptions partent dans la distribution PyPI.

## Décisions et alternatives écartées

- **Écrire chaque connecteur partagé deux fois à la main** : écarté le 29/09, les deux versions divergeraient. Il reste l'exception, pour un connecteur hors format.
- **Un service connecteurs séparé, en Python, sans état, le secret dans un en-tête** : abandonné le 29/09. Les connecteurs s'exécutent dans le paquet, chez l'hôte ; le détail et ses raisons sont dans oto-pkg : docs/conception/connecteurs-et-comptes.md.
- **La fabrique dans le paquet, en TypeScript** : écarté. Elle vit à côté des descriptions et de la lib qu'elle génère, pour qu'une seule source fasse foi.
- **Laisser au backend d'oto 1 ses outils écrits à la main jusqu'à son extinction** : décidé le 29/09, retiré le 06/10 ; le double durerait autant qu'oto 1.
- **Lire la description en gardant les noms et regroupements d'outils d'oto 1** (`op=`) : écarté le 06/10 ; le format porterait un vocabulaire qui disparaîtra avec oto 1.
- **Partir de zéro, sans la lib** : écarté le 30/09. La lib est gardée pour les connecteurs partagés, au secret fourni par le consommateur.
- **TypeScript seul, la lib Python gelée jusqu'à l'extinction d'oto 1** : écarté le 06/10. Une seule version des connecteurs passe par la description et la fabrique, pas par l'abandon d'un des deux langages ; la description devient la source du client Python comme du TypeScript, et un client Python écrit à la main à côté d'elle est un doublon à résorber.
- **Python seul, appelé à distance par oto 2** : écarté de nouveau le 06/10, pour les raisons du service connecteurs abandonné le 29/09.
- **La fabrique avant la prise du paquet** : écarté le 06/10. Le paquet d'oto 2 reçoit d'abord sa prise (contrat de connecteur, secret à l'appel, comptes réels), prouvée par un connecteur témoin écrit à la main au contrat du paquet, `notion` ; la fabrique génère ensuite ce même contrat et remplace le témoin.

## Sécurité et confidentialité

- Le code généré ne lit aucun secret, ni dans l'environnement ni dans un fichier. Le Python généré vit sous `oto/` et tombe sous la garde `tests/test_no_secret_read_guard.py`.
- Un secret ne part jamais dans la query d'une URL : il finirait dans les messages d'erreur et les journaux. La fabrique le place en en-tête ou dans le corps de la requête de jeton, comme `auth` le décrit.
- Un refus généré dit le fait, au plus une condition, jamais le nom d'un outil (`docs/conventions.md`).
- Le dépôt et ses sorties sont publics : aucune description, aucun code généré ne nomme un client.

## Écart avec le code

- Aucune fabrique : ni générateur, ni runtime commun dans l'un ou l'autre langage, ni paquet npm de connecteurs, ni test de contrat entre les deux langages. Seuls existent le schéma, douze descriptions et leur test de validation ([format de description](format-de-description.md), « Les connecteurs décrits »).
- Les deux règles hors schéma JSON ne sont vérifiées nulle part.
- Le backend d'oto 1 n'a pas de lecteur de la description : tous ses outils restent déclarés à la main.
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
- 2026-09-29 : la fabrique vit dans ce dépôt, en Python ; elle génère la lib Python et publie la sortie TypeScript en paquet npm, que le paquet d'oto 2 déclare et inscrit ; le backend d'oto 1 est hors périmètre (retiré le 06/10) — décidé par le mainteneur (source : séance du 29/09, « Périmètre, sortie TypeScript, secrets »).
- 2026-09-29 : la lib ne lit plus aucun secret : résolution locale et ses quatre fournisseurs retirés, secret exigé par `require` ; publié en 1.148.0 — décidé par le mainteneur (source : séance du 29/09).
- 2026-09-30 : la lib est gardée pour les connecteurs partagés, au secret fourni par le consommateur — décidé par le mainteneur, accord du responsable du paquet à l'oral (source : point du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
- 2026-10-06 : une seule version des connecteurs, par la description et la fabrique ; TypeScript seul et Python à distance écartés ; la prise du paquet d'abord, avec un témoin `notion` écrit à la main — décidé par le mainteneur.
- 2026-10-06 : le backend d'oto 1 bascule connecteur par connecteur sur la description, par un lecteur générique ; ses noms d'outils changent connecteur par connecteur — décidé par le mainteneur.
