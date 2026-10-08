# La fabrique

- **Statut** : proposé
- **Dernière révision** : 2026-10-08

## Résumé

La fabrique lit les [fichiers de description](format-de-description.md) et génère, pour chaque connecteur partagé, des définitions TypeScript structurelles et typées, que le paquet d'oto 2 adapte à son contrat de fonction et exécute ; puis, dans un second temps, un client Python dans la lib. Elle vit dans ce dépôt, à côté des descriptions, écrite en Python (`fabrique/`) ; sa sortie TypeScript est commitée sous `ts/`. L'application hôte (oto-saas) en dépend et déclare au paquet d'oto 2 les connecteurs qu'elle choisit ; le paquet, moteur générique, n'en contient aucun. La sortie TypeScript existe ; la sortie Python, pas encore.

## Contexte

- Oto 2 connaît deux sortes de connecteurs. Un connecteur propre à un hôte s'écrit dans l'hôte, à la main, au contrat de fonction du paquet. Un connecteur partagé a sa source ici et passe par la fabrique. Le contrat côté hôte (fonction du catalogue, comptes, secret fourni à l'appel, coffre) est décrit dans oto-pkg : docs/conception/connecteurs-et-comptes.md ; ce document ne le répète pas.
- Le langage servi dépend du consommateur, pas du connecteur : Python pour tout consommateur écrit en Python, TypeScript pour le paquet d'oto 2.
- Côté paquet, au relevé du 29/09 : les fonctions s'inscrivent par une liste statique d'imports, la source TypeScript est transpilée par l'hôte, les dépendances sont épinglées. Pour une fonction de connecteur, le paquet reçoit le JSON Schema d'entrée et le valide lui-même, avec un validateur JSON Schema standard.
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

Chaque `connectors/<nom>/connector.yaml`, validé contre `connectors/connector.schema.json`, puis les règles que le schéma JSON ne sait pas dire :
1. chaque argument d'`input` va à un seul endroit de `call` (chemin, query, corps ou en-tête) ;
2. chaque secret que nomme `auth` est un champ de `credential` ;
3. un nom côté API reçoit une seule valeur : une constante ne reprend pas le nom d'un argument de la même place, un en-tête n'est posé qu'une fois (authentification, en-têtes du connecteur, de la fonction, à la casse près), une clé en query n'est le nom d'aucun paramètre de query, et `encode` ne nomme qu'un argument placé en query, en corps ou en en-tête ;
4. un contrôle (`checks`, `expect`) nomme un refus de sa fonction, et `equal_sums` une liste dont les éléments déclarent les deux champs ;
5. la sonde, et l'identité d'un `oauth2_user`, nomment une fonction de lecture, appelée par `call`, sans argument requis ;
6. un nom n'est déclaré qu'une fois parmi `credential`, `settings` et `from_token` ; le défaut d'une liste fermée en est une valeur ; un gabarit ne cite que des réglages déclarés (et `base_url`, des valeurs de `from_token`), une adresse libre en tête seulement, un autre réglage après `https://` seulement ; `base_urls` et `token_urls` portent sur une liste fermée et donnent une adresse à chacune de ses valeurs ; tout réglage est cité.

Le schéma refuse lui-même un `credential` en `oauth2_user` : les jetons d'un consentement ne sont pas des champs.

Une règle violée fait refuser le fichier, et rien n'est généré.

### Ce qu'elle génère

| Sortie | Contenu |
|---|---|
| Python, dans la lib | Une méthode par fonction sur le client du connecteur, au-dessus d'un runtime commun : transport, authentification par `auth.kind`, encodage des listes (`query_arrays`), pagination. |
| TypeScript, sous `ts/src/` | Un module par connecteur : sa définition (authentification telle que décrite, clés en camelCase, `baseUrl` ou `baseUrls`, champs du credential, réglages, table d'erreurs, délai en millisecondes, exposition, en-têtes constants, rythme maximal, sonde) et une définition par fonction (nom qualifié `<connecteur>.<fonction>`, classe, description, schéma d'entrée en JSON Schema, exemples avec leur titre, refus en table `code`/`when`/`message`, contrôles avant l'appel et sur la réponse, spécification de requête avec constantes et encodages, pagination, sortie, coût, résumé de confirmation) ; un index qui les liste. Le contrat de ces définitions est un module écrit à la main, `ts/src/types.ts`. |
| Les deux | La table des erreurs et les `refusals`, en table ; les tests de contrat. |

- La sortie TypeScript ne dépend ni du paquet d'oto 2 ni d'aucune bibliothèque : pas de cycle. Le paquet l'adapte à son contrat (refus en chaînes, exemples sans titre, codes d'erreur à lui) et l'exécute avec son client HTTP, son coffre et son journal.
- La spécification de requête dit la méthode, le chemin et ses `{param}`, et pour `query`, `body` et `headers` le nom côté API de chaque argument ; la sortie ne contient aucun code qui envoie une requête.
- Le schéma d'entrée est le JSON Schema 2020-12 de la description, recopié tel quel une fois les ancres YAML résolues : le schéma servi et le schéma validé sont le même objet. L'hôte le valide avec un validateur standard ; le test de la sortie le fait avec Ajv 2020, `format` restant une annotation comme dans le test des descriptions. La fabrique n'en vérifie qu'une chose, que la racine est un objet strict (`type: object`, `additionalProperties: false`) ; une fonction qui y manque, ou écrite à la main, n'est pas générée : elle est nommée, avec sa raison, en tête du module et à la sortie de la commande.
- La pagination ajoute `all_pages` et `max_pages` au schéma servi ; `max_pages` borne le parcours, que l'hôte mène.
- La sortie décrit, l'hôte exécute : il valide l'entrée contre le schéma ; pose les en-têtes constants du connecteur et de la fonction, puis les constantes de query et de corps ; sérialise en JSON les arguments que nomme `encode` ; envoie le corps quelle que soit la méthode, `DELETE` compris ; tient le rythme maximal par credential ; suit la pagination jusqu'à `next` vide ou `more` faux ; joue les `checks` avant l'appel et les `expect` sur la réponse, en rendant le refus nommé ; appelle la sonde avec `{}`, sans la décompter d'un quota, et exige non vides les chemins de `nonEmpty`.
- Pour le compte, l'hôte : propose à la connexion les champs et les réglages (liste fermée, motif) ; résout l'adresse (gabarit ou `baseUrls`) ; garde une adresse libre (`https` seul, hôte qui ne résout jamais vers une adresse interne, à chaque résolution, aucune redirection suivie) et ne substitue une valeur `text` ou `choice` que faite de lettres, chiffres, `-` et `_` ; pose la clé en en-tête ou en query, masquée dans tout journal ; envoie l'identifiant et le secret du client selon `clientAuth`.
- Pour `oauth2_user`, l'hôte mène le consentement avec son application OAuth, ou celle de l'organisation : adresse d'autorisation (scopes joints par des espaces, `authorizeParams`, `state`, PKCE), échange du code en formulaire, lecture de l'identité qui nomme le compte, valeurs de `fromToken` gardées ; il renouvelle selon `refresh`, et sous `rotates` réécrit le refresh token rendu, seulement si le jeton stocké est encore celui qu'il a lu.
- `python -m fabrique` régénère `ts/src/` ; `python -m fabrique --check` échoue si la sortie commitée n'est pas à jour. On la lance à la main ; la CI vérifie, par `tests/test_fabrique.py`, que la sortie commitée est à jour, et compile et teste `ts/`.
- La projection suit `exposure.mode` : en `per_action`, un outil `<namespace>_<name>` par fonction, schéma tel quel ; en `per_connector`, un outil qui reçoit `action` (l'énumération des noms) et un schéma en `oneOf` des entrées ; en `via_call`, rien n'est exposé et le verbe d'appel lit le registre.
- Tests de contrat : le schéma d'entrée servi est le même des deux côtés ; chaque exemple est joué contre le schéma et contre un serveur simulé.

### Ce qu'elle ne génère pas

- Le corps d'une entrée `handwritten`.
- Le runtime commun, écrit une fois par langage : consentement et renouvellement, cache de jeton (au niveau du processus, indexé par une empreinte du credential, jamais par le secret en clair), rejeu unique sur 401, limiteur de débit partagé.
- La fusion de plusieurs appels, un refus local que les contrôles déclarés ne disent pas, la construction d'un mail, l'identité opérée.
- Le texte d'un résultat : le paquet le compose, dans son adaptateur.

### Le secret

Le code généré ne lit aucun secret. En Python, le constructeur du client reçoit les champs de `credential` et les exige par `require`. En TypeScript, le secret vient du contexte d'appel que l'hôte fournit, une fois le compte résolu (oto-pkg : docs/conception/connecteurs-et-comptes.md). Un appel sans secret échoue ; aucune clé par défaut ne le remplace.

### Le nom d'un connecteur

`connector.name` est la clé par laquelle un hôte référence le connecteur. Côté oto 2, le catalogue des connecteurs est une table et toute référence y est une clé étrangère, pour qu'un renommage ne fasse jamais disparaître des outils en silence (leçon d'oto 1, où une installation au nom inconnu était ignorée au chargement). Renommer un connecteur est donc une migration chez l'hôte, jamais une simple édition du fichier.

### Livraison

- La lib se publie sur PyPI, sur un tag posé à la main (`docs/release.md`).
- La sortie TypeScript (`ts/`, nom provisoire `@otomata_tech/connectors`, privé) n'est pas publiée : l'application hôte en dépend par un commit de ce dépôt, épinglé. Le paquet d'oto 2 n'en dépend pas ; il accepte des définitions de même forme, que l'hôte lui déclare. Une publication npm, si elle vient, part sur un tag posé à la main avec l'accord du mainteneur.

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
- **Interdire un secret dans la query d'une adresse** : révisé le 2026-10-08. Certains tiers n'acceptent la clé qu'en query ; elle y est admise pour `api_key` seulement, parce que le tiers la voit déjà, et l'exécution la masque dans tout journal. Les autres secrets (secret du client, code, refresh token) restent dans le corps.
- **L'authentification `oauth2_user` écrite à la main par connecteur** : écarté le 2026-10-08. Elle se décrit ; l'hôte l'exécute une fois pour tous, comme la requête.
- **Le paquet d'oto 2 dépend de la sortie et embarque des connecteurs** : écarté le 07/10. Le paquet est le moteur ; l'application hôte choisit ses connecteurs partagés et propres, et les lui déclare.
- **Une sortie TypeScript au contrat du paquet** (fonctions `defineFunction` prêtes à inscrire) : écarté le 06/10. Elle dépendrait du paquet, qui dépendrait d'elle ; la sortie porte des définitions structurelles, que le paquet adapte.
- **Une sortie produite à la publication, non commitée** : écarté le 06/10. Commitée, elle se relit dans une revue et un test prouve qu'elle suit les descriptions.
- **Traduire le schéma d'entrée en `zod` strict** : écarté le 06/10. Trente-sept fonctions sur 340 ne se traduisaient pas (`minProperties`, `oneOf`, `anyOf` ou `not` à la racine, `dependentRequired`, `not` sur un identifiant, `uniqueItems`) ; un raffinement ne se voit pas dans le schéma servi, et deux langages de schéma devaient rester égaux. Le JSON Schema passe tel quel, validé par l'hôte.
- **La fabrique avant la prise du paquet** : écarté le 06/10. Le paquet d'oto 2 reçoit d'abord sa prise (contrat de connecteur, secret à l'appel, comptes réels), prouvée par un connecteur témoin écrit à la main au contrat du paquet, `notion` ; la fabrique génère ensuite ce même contrat et remplace le témoin.

## Sécurité et confidentialité

- Le code généré ne lit aucun secret, ni dans l'environnement ni dans un fichier. Le Python généré vit sous `oto/` et tombe sous la garde `tests/test_no_secret_read_guard.py`.
- Un secret ne part dans la query d'une adresse que si le tiers l'exige (`api_key`, `in: query`) : le tiers le voit déjà, et l'exécution le masque dans tout journal, message d'erreur compris. Tout autre secret va en en-tête ou dans le corps de la requête de jeton, comme `auth` le décrit.
- Une adresse saisie par un admin ou rendue par un jeton est gardée à l'exécution : `https` seul, jamais un hôte interne, aucune redirection.
- Une constante ou un en-tête constant est écrit en clair dans un fichier public : jamais un secret, et jamais l'en-tête d'authentification, que la règle 3 refuse de poser deux fois.
- Un refus généré dit le fait, au plus une condition, jamais le nom d'un outil (`docs/conventions.md`).
- Le dépôt et ses sorties sont publics : aucune description, aucun code généré ne nomme un client.

## Écart avec le code

- Écrits : le générateur (`fabrique/`), la sortie TypeScript commitée (`ts/src/`, treize connecteurs) et son contrat (`ts/src/types.ts`), leurs tests (`tests/test_fabrique.py`, `ts/test/`) et le contrôle en CI. Les six règles hors schéma JSON sont vérifiées.
- Les 340 fonctions sont générées.
- Pas de sortie Python, ni de runtime commun, ni de test de contrat entre les deux langages. Le client Python Sellsy reste écrit à la main, sur des verbes génériques.
- Le paquet d'oto 2 n'adapte pas encore la sortie : son client HTTP ne connaît ni `PUT`, ni la query, ni un autre mode d'authentification que le jeton porteur ; il n'exécute encore ni la validation JSON Schema, ni les constantes, l'encodage, le rythme, la sonde ou les contrôles, ni les réglages, la clé en query, `clientAuth`, le consentement `oauth2_user` et sa rotation, ni les gardes d'une adresse libre.
- Les gardes d'une adresse libre ne sont écrites nulle part dans ce dépôt : la sortie les déclare, aucun code ne les exécute.
- Le backend d'oto 1 n'a pas de lecteur de la description : tous ses outils restent déclarés à la main.

## Questions ouvertes

- Pendant la bascule d'oto 1 vers oto 2, un connecteur utile sans description passe-t-il par un doublage à la main ?

## Historique

- 2026-09-29 : une description par connecteur et une fabrique qui génère Python et TypeScript ; le doublage à la main reste l'exception — décidé par le mainteneur (source : séance du 29/09 ; conception connecteurs d'oto 2, oto-enterprise, archivé).
- 2026-09-29 : la fabrique vit dans ce dépôt, en Python ; elle génère la lib Python et publie la sortie TypeScript en paquet npm, que le paquet d'oto 2 déclare et inscrit ; le backend d'oto 1 est hors périmètre (retiré le 06/10) — décidé par le mainteneur (source : séance du 29/09, « Périmètre, sortie TypeScript, secrets »).
- 2026-09-29 : la lib ne lit plus aucun secret : résolution locale et ses quatre fournisseurs retirés, secret exigé par `require` ; publié en 1.148.0 — décidé par le mainteneur (source : séance du 29/09).
- 2026-09-30 : la lib est gardée pour les connecteurs partagés, au secret fourni par le consommateur — décidé par le mainteneur, accord du responsable du paquet à l'oral (source : point du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
- 2026-10-06 : une seule version des connecteurs, par la description et la fabrique ; TypeScript seul et Python à distance écartés ; la prise du paquet d'abord, avec un témoin `notion` écrit à la main — décidé par le mainteneur.
- 2026-10-06 : le backend d'oto 1 bascule connecteur par connecteur sur la description, par un lecteur générique ; ses noms d'outils changent connecteur par connecteur — décidé par le mainteneur.
- 2026-10-06 : la sortie TypeScript est commitée dans ce dépôt (`ts/`), en définitions structurelles qui ne dépendent pas du paquet ; schéma d'entrée en `zod` strict généré, refus en table, texte du résultat composé par le paquet ; première version du générateur — décidé par le mainteneur.
- 2026-10-06 : plus de `zod` dans la sortie : le JSON Schema d'entrée est transmis tel quel et validé par l'hôte avec un validateur standard ; la fabrique n'exige qu'une racine stricte, et génère les 340 fonctions ; la sortie porte en-têtes constants, constantes, encodage JSON, arrêt de pagination, rythme, sonde et contrôles — décidé par le mainteneur.
- 2026-10-07 : l'application hôte dépend de la sortie par un commit épinglé et déclare ses connecteurs au paquet d'oto 2, moteur générique sans connecteur ; pas de publication npm pour l'instant — décidé par le mainteneur.
- 2026-10-08 : la fabrique vérifie les réglages, les gabarits et l'identité d'un consentement, et porte telles quelles dans la sortie l'authentification (`oauth2_user` déclaratif compris) et les réglages ; une clé est admise en query, masquée à l'exécution — décidé par le mainteneur.
