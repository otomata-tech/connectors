# Format de description d'un connecteur

- **Statut** : validé avec Alexis le 29/09/2026
- **Dernière révision** : 2026-10-06

## Résumé

Un connecteur partagé se décrit dans un fichier YAML, `connectors/<nom>/connector.yaml`, validé par `connectors/connector.schema.json` (JSON Schema 2020-12). L'unité du format est la fonction servie avec son appel : ce que l'agent voit (nom, description en anglais, schéma d'entrée strict, classe, exemples, refus nommés) et la requête derrière (méthode, chemin, répartition des arguments, pagination, sortie). Ce qui ne se décrit pas reste du code écrit à la main, référencé depuis le fichier. La [fabrique](fabrique.md) en tire Python et TypeScript ; treize connecteurs ont leur description, et tout client ajouté à la lib entre au format.

## Contexte

- La lib porte une centaine de clients Python écrits à la main. Au relevé du 29/09, le schéma d'un outil y vient de la signature et sa description de la docstring ; aucune classe n'est déclarée, aucun refus n'est nommé, la confirmation se fait au cas par cas.
- Le paquet d'oto 2 sert des fonctions qui déclarent classe, exemples, refus et résumé de confirmation. Elles ne sont pas des outils MCP : elles se trouvent, se lisent et s'appellent par les six outils figés du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Un connecteur partagé sert deux langages : Python pour la lib, TypeScript pour le paquet. Écrit deux fois à la main, il divergerait ; d'où une description unique.
- Relevé du 29/09 sur 114 connecteurs de la lib, par lecture du code et à seuil arbitraire : 54 enveloppes d'API REST, 44 REST avec logique, 16 hors format (3 navigateur, 7 données ouvertes ou base embarquée, 3 SDK, 2 traitement local, 1 asynchrone).
- Se décrit : authentification simple, URL de base, verbe et chemin, répartition des arguments, schéma, table des erreurs et leur caractère rejouable, projection de sortie, règle de coût, pagination. Reste à la main : rafraîchissement OAuth d'un utilisateur, limiteur de débit partagé, fusion de plusieurs appels, refus locaux avant l'appel, construction d'un mail, identité opérée.

## Objectifs et non-objectifs

- Une seule source par connecteur partagé, d'où sortent les deux langages.
- Le contrat vu de l'agent s'écrit une fois : classe, exemples validés, refus nommés, en anglais.
- Un fichier invalide est refusé avant toute génération.
- Ce qui ne se décrit pas reste possible, par une entrée `handwritten` dont le schéma, la classe et les refus restent déclaratifs.
- Hors objectif : convertir les connecteurs existants. Ils restent en Python tels quels ; seuls les nouveaux connecteurs partagés et ceux qu'oto 2 demande entrent au format, et un existant réclamé entre comme un nouveau.
- Hors objectif : décrire un connecteur propre à un hôte, écrit dans l'hôte au contrat du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Hors objectif : porter un secret, un compte ou une activation. Le fichier nomme les champs du credential ; le reste relève de l'hôte.

## Conception

### Le fichier

- Un dossier par connecteur sous `connectors/` : son `connector.yaml` et, si besoin, son code écrit à la main dans les deux langages. `connector.name` est le nom du dossier.
- YAML, déclaré en version 1.2, validé par un schéma JSON 2020-12 que les deux langages savent lire. Le fichier porte en tête `# yaml-language-server: $schema=../connector.schema.json`.
- Trois blocs : `connector` (un par fichier), `functions` (au moins une), `exposure` (facultatif). Toute clé inconnue fait refuser le fichier.

### Le bloc `connector`

| Champ | Obligatoire | Sens |
|---|---|---|
| `name` | oui | Identifiant stable (`^[a-z][a-z0-9_]*$`), premier segment des outils. |
| `label` | oui | Nom affiché. |
| `namespace` | oui | Préfixe des outils exposés, souvent égal à `name`. |
| `version` | oui | Version du fichier, semver. |
| `api_version` | oui | Version de l'API tierce, telle que l'éditeur la nomme. |
| `base_url` | sauf si tout est `handwritten` | Racine `https://` des chemins d'appel, sans `/` final. |
| `auth` | oui | Mode d'authentification, voir ci-dessous. |
| `credential` | sauf `auth.kind: none` | Champs fournis par le consommateur : `name`, `label`, `secret` (booléen). |
| `modes` | oui | Qui peut porter un compte : `platform`, `byo_user`, `byo_org`. |
| `timeout_s` | non (30) | Délai d'un appel, en secondes. |
| `quota` | non | Quota par défaut sur une clé de la plateforme ; n'existe que si `modes` contient `platform`. |
| `query_arrays` | non | Encodage des listes en query : `repeat`, `brackets`, `comma`. |
| `errors` | oui | Table commune à toutes les fonctions : `status` (un ou plusieurs codes HTTP), `code` (refus nommé), `message` (anglais), `retryable`. |

`auth.kind` vaut `api_key` (`header`, `prefix` facultatif, `key`), `bearer` (`token`), `basic` (`username`, `password`), `oauth2_client_credentials` (`token_url`, `token_request` `json` ou `form`, `client_id`, `client_secret`, `scope` et `expires_in_default` facultatifs), `oauth2_user` (renvoie à `handwritten`) ou `none`. Chaque valeur qui désigne un secret nomme un champ de `credential`, jamais une valeur.

### Le bloc `functions`

| Champ | Obligatoire | Sens |
|---|---|---|
| `name` | oui | Un verbe et son objet : `list_estimates`. |
| `class` | oui | `read`, `write` ou `sensitive`. |
| `description` | oui | En anglais : ce que l'agent lit. |
| `input` | oui | JSON Schema d'un objet strict (`type: object`, `additionalProperties: false`, `properties`). |
| `examples` | oui, au moins un | `title` et `input`, chaque `input` validé contre `input`. |
| `refusals` | non | `code`, `when` (statut HTTP ou condition), `message` : surcharge ou complète la table commune. |
| `call` | sauf `handwritten` | `method`, `path` relatif avec `{param}`, puis `query`, `body`, `headers` qui associent un nom côté API à un argument. Chaque argument va à un seul endroit. |
| `handwritten` | exclusif de `call` | `python` (`module`, `function`) et `typescript` (`file`, `export`). |
| `pagination` | non | `kind` (`cursor` ou `page`), `request_param`, `next`, `total` facultatif, `max_pages`. Présente, elle ajoute `all_pages` et `max_pages` au schéma servi. |
| `output` | non | `items` (chemin de la liste), `strip` (clés retirées), `projection` (clés gardées). |
| `cost` | non | Unité décomptée : `per: request` ou `per: page`. |
| `confirm` | si `sensitive` | `summary`, gabarit du résumé montré avant exécution (`Validate invoice {id}: irreversible.`). |

### Le bloc `exposure`

L'exposition est une projection, pas une propriété de la fonction. `mode` vaut `per_action` (un outil `<namespace>_<name>` par fonction), `per_connector` (un outil par connecteur, l'action en paramètre) ou `via_call` (rien n'est exposé, le verbe d'appel lit le registre). Le défaut est `per_connector`, fixé par le consommateur ; `mode` le surcharge pour un connecteur, `tool` nomme l'outil unique en `per_connector`.

### Le premier fichier : Sellsy

`connectors/sellsy/connector.yaml` décrit deux fonctions de lecture, `list_estimates` (pagination par curseur, dix pages au plus) et `get_estimate` (refus `estimate_not_found` sur 404), derrière `oauth2_client_credentials` en corps JSON. Ses valeurs ont été vérifiées le 30/09 contre la référence publique de l'API Sellsy v2 (spec v2.300.0) : champs de tri, `embed` de la liste et du détail, coût par requête, requête de jeton.

```yaml
functions:
  - name: get_estimate
    class: read
    description: >-
      Get the full record of one Sellsy estimate by its id.
    input:
      type: object
      additionalProperties: false
      required: [id]
      properties:
        id: { type: integer }
    examples:
      - title: One estimate with its company
        input: { id: 42, embed: [company] }
    refusals:
      - { code: estimate_not_found, when: 404, message: "No estimate with this id in this Sellsy account." }
    call: { method: GET, path: "/estimates/{id}", query: { field: fields, embed: embed } }
```

(Extrait raccourci : le fichier déclare aussi `fields` et `embed` dans `input`.)

### Les connecteurs décrits

Treize connecteurs ont leur `connector.yaml` (nombre de fonctions) : `affinity` (33), `aircall` (18), `amplitude` (16), `claap` (6), `mailpool` (21), `meta_ads` (8), `microsoft` (13), `nextmotion` (139), `notion` (22), `pennylane` (48), `sellsy` (2), `typeform` (4), `wttj_ats` (10). Chaque valeur vient du client Python ou de la référence publique de l'éditeur ; quand les deux divergent, le fichier suit le client. Une fonction que le format ne sait pas dire reste hors du fichier plutôt que d'y être approchée : ainsi `mailpool.update_domain_dns`, dont le corps est un tableau d'enregistrements.

`pennylane` (API v2, une clé par société) couvre ce que couvre le client : référentiels, clients, fournisseurs, factures clients et avoirs, devis, factures d'achat (lecture, correction, validation), grand livre et lettrage, transactions, balance, rapprochement ; 26 lectures, 13 écritures, 9 fonctions sensibles. Valeurs vérifiées le 2026-10-06 contre l'OpenAPI publique « Company V2 ». Restent hors du fichier : le téléversement de pièce (multipart) et l'import de facture d'achat qui en dépend, les deux recherches anti-doublon par `external_reference` du client (servies par le `filter` des listes), l'agrégat `fetch_complete_data` (plusieurs appels) et l'option `only_outstanding` des transactions (filtre local). Trois gestes du client s'écrivent autrement, fidèles à l'API : le `filter` est une chaîne JSON que l'agent écrit ; `draft` est un argument requis de valeur constante `true` ; un avoir prend des quantités négatives, imposées par le schéma, là où le client inverse le signe.

Quatre clients n'ont pas de fichier, aucune de leurs fonctions n'étant descriptible : `threecx` (adresse du standard propre à chaque compte, connexion hors OAuth2 standard, audio binaire), `boondmanager` (jeton signé à chaque requête), `bigquery` (SDK et OAuth utilisateur), `wordpress` (adresse propre à chaque site, fournie par le credential ; racine REST découverte à l'appel, `/wp-json/` ou `?rest_route=` ; téléversement de média en corps binaire).

### Contrôles

`tests/test_connector_descriptions.py` vérifie que le schéma est un JSON Schema valide, que chaque description le respecte et porte le nom de son dossier, et que chaque exemple respecte l'entrée de sa fonction. Quinze variantes invalides du fichier Sellsy doivent être refusées : classe absente, clé inconnue, `call` et `handwritten` ensemble ou aucun des deux, fonction sensible sans `confirm`, entrée non stricte, aucun exemple, exemple hors bornes ou à argument inconnu, `embed` non documenté, `token_url` absent, `credential` absent, `quota` sans `platform`, version hors semver, `exposure.mode` inconnu. `jsonschema` est un outil de test, pas une dépendance de la lib.

## Décisions et alternatives écartées

- **Du code source dans l'un des deux langages** : écarté le 29/09. L'autre langage n'en serait qu'une traduction ; un fichier déclaratif se lit des deux côtés.
- **TOML** : écarté, illisible dès trois niveaux d'imbrication, qu'un schéma d'entrée dépasse souvent. **JSON seul** : écarté, sans commentaires et pénible pour une description sur plusieurs lignes.
- **Référencer la spec OpenAPI de l'éditeur** (`operation: getEstimates`) et n'ajouter que classe, refus, exemples et projection : non retenu à ce jour. Moins à écrire, mais chaque connecteur dépendrait de la qualité d'une spec tierce.
- **Un client ajouté à la lib sans description** : écarté le 2026-10-05. Tout client entre au format ; une fonction ou un client que le format ne sait pas dire est nommé, avec sa raison, dans « Les connecteurs décrits ».
- **L'outil MCP comme unité** (le modèle d'oto 1, où le nom de l'outil est le contrat et où Sellsy est servi par `sellsy_document(kind, op)`, quatre documents de vente fusionnés) : écarté. L'unité est la fonction ; l'outil, une projection. Cela répond au point « verbe `oto_call` ou outils dédiés » d'ADR 0070 §7.8 : les deux, par projection.
- **Refus en texte libre** : écarté. Une table structurée (`code`, `when`, `message`) que la fabrique traduit en erreurs typées dans les deux langages.
- **Une seule version** : écarté. La version du fichier (semver) et celle de l'API tierce (en clair) sont deux champs.
- **Convertir les 713 outils existants, ou leur attribuer une classe** : écarté. La classe s'écrit à l'entrée d'un connecteur dans le format.
- **Garder les descriptions en français** : écarté. Ce que lit l'agent est en anglais ; une description française se traduit à son entrée.

## Sécurité et confidentialité

- Le fichier ne porte aucun secret : il nomme les champs du credential (`secret: true`) et `auth` y renvoie par leur nom. Le secret est fourni par le consommateur à chaque appel ; la lib n'en lit aucun (garde `tests/test_no_secret_read_guard.py`).
- `base_url` nomme la racine documentée de l'API de l'éditeur. Une coordonnée tirée du front d'un tiers (clé d'API publique, identifiant d'application) n'entre jamais dans un fichier (`docs/conventions.md`).
- Le dépôt est public : un connecteur dont l'accès appartient à un client, son propre back-office, n'a pas de description ici ; c'est un connecteur propre à son hôte.
- Un message de refus dit le fait, au plus une condition, jamais le nom d'un outil : la lib ne connaît pas les outils de l'appelant.

## Écart avec le code

- Écrits : le schéma, son test et treize descriptions (« Les connecteurs décrits ») ; le schéma et Sellsy depuis la version 1.149.0 de la lib, dix autres depuis le 2026-10-05, `mailpool` ensuite, `pennylane` le 2026-10-06. Aucun test n'impose encore qu'un client ait sa description, et le client `wordpress`, ajouté en 1.156.0, n'en a pas.
- Les descriptions ne partent pas dans la distribution PyPI : ni la roue ni l'archive source de la 1.154.0 ne les contiennent. Seul le dépôt les porte.
- Deux règles échappent au schéma JSON et ne sont vérifiées nulle part : un argument va à un seul endroit (et chaque `{param}` du chemin nomme un argument) ; une référence de `auth` désigne un champ de `credential` existant. La fabrique doit les vérifier.
- `modes` garde le vocabulaire d'oto 1 (`platform`, `byo_user`, `byo_org`).
- `quota` n'a pas de forme : le schéma n'exige qu'un objet non vide.
- Le format se déclare YAML 1.2, mais le test lit avec PyYAML, qui suit YAML 1.1.
- Le client Python Sellsy de la lib reste écrit à la main, sur des verbes génériques ; rien n'est généré.
- Les clients Python restent écrits à la main ; leurs descriptions, écrites après eux, n'en sont pas encore la source.
- `pennylane` s'écarte du client là où le client lit une seule page d'une liste paginée (exercices, catégories, lignes de facture et de devis) : le fichier les décrit paginées, comme l'OpenAPI. Un `DELETE` y porte un corps (délettrage) : le schéma l'accepte, la fabrique devra l'envoyer.

## Questions ouvertes

- La forme de `quota`.
- Le vocabulaire de `modes` : organisation, équipe et personne, ou le retirer du fichier, puisque le compte relève de l'hôte ?
- YAML 1.2 déclaré, lecteur 1.1 : changer de lecteur, ou se limiter au sous-ensemble commun et le dire ?
- Référencer la spec OpenAPI de l'éditeur quand elle existe ?
- Le défaut `per_connector` vaut-il pour un hôte d'oto 2, dont les fonctions ne passent que par `call` ?
- Ce que le format ne sait pas encore dire, relevé en décrivant dix connecteurs le 2026-10-05 :
  - une adresse propre au compte ou à la région (`threecx`, `typeform` hors des États-Unis, `amplitude` en Europe) ;
  - un en-tête constant (`Notion-Version`, `X-Affinity-Api-Version`) ;
  - une constante ou un tableau en corps (`affinity`, `nextmotion`, `microsoft`), un corps en formulaire (`meta_ads`) ;
  - une réponse autre que JSON (CSV, fichier binaire), une pagination par adresse complète (`@odata.nextLink`) ;
  - une signature par requête (`boondmanager`) ;
  - `handwritten` exige un fichier TypeScript, qui n'existe pour aucun connecteur ;
  - un coût par élément d'un lot, ou relu dans la réponse de l'amont : `cost` ne connaît que `per: request` et `per: page`, alors qu'un lot payant se facture au contact soumis, ou seulement à la donnée trouvée, chiffrée dans la réponse ;
  - une sonde déclarée : quelle fonction vérifie la connexion, et ce qu'elle couvre (l'authentification seule, ou l'authentification et le quota), sans rien facturer.
- Relevé en décrivant `pennylane` le 2026-10-06 :
  - un argument structuré encodé en JSON dans la query (`filter=[{"field","operator","value"}]`), et des clauses de filtre bâties depuis des arguments nommés (bornes de date, statut, `external_reference`) ;
  - un débit maximal à respecter (environ quatre requêtes par seconde) : la fabrique prévoit un limiteur partagé, le fichier ne sait pas en donner le rythme ;
  - un drapeau d'arrêt de pagination distinct du curseur (`has_more`) ;
  - un contrôle local avant l'appel qui porte sur une somme (débits égaux aux crédits d'une écriture) ;
  - une sonde qui vérifie aussi les droits : `GET /me` authentifie, mais une clé sans aucun `scopes` ne peut rien faire.
- Un test doit-il refuser un client sans description, avec une liste nommée d'exceptions ?

## Historique

- 2026-09-29 : le connecteur partagé se décrit dans un fichier déclaratif ; l'unité est la fonction servie avec son appel ; l'exposition est une projection ; les descriptions sont en anglais — décidé par le mainteneur (source : séance du 29/09 ; conception connecteurs d'oto 2, oto-enterprise, archivé).
- 2026-09-29 : YAML validé par un schéma JSON ; trois blocs ; refus en table structurée ; version du fichier et version de l'API séparées ; projection par défaut `per_connector` ; existants non convertis, Sellsy d'abord — décidé par le mainteneur (source : séance du 29/09, « Le fichier de description »).
- 2026-09-30 : schéma `connector.schema.json` et fichier Sellsy vérifié contre la référence publique de l'API ; ajouts : le champ de `auth` qui nomme le secret (`key`, `token`, `username`, `password`), `scope` en OAuth2, l'erreur 400 `invalid_request` — choix du projet (source : lot 2 du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
- 2026-10-05 : tout client ajouté à la lib entre au format ; dix connecteurs décrits, trois non descriptibles en l'état, et la liste de ce que le format ne sait pas encore dire — décidé par le mainteneur (source : séance du 05/10).
- 2026-10-06 : description `pennylane`, 48 fonctions, et cinq trous du format relevés — choix du projet.
- 2026-10-05 : la description microsoft passe à l'accès délégué (jeton d'une personne, fourni par l'hôte), version 2.0.0 — choix du projet (source : refonte du client, v1.155.0).
