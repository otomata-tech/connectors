# Format de description d'un connecteur

- **Statut** : validé par le mainteneur le 29/09/2026
- **Dernière révision** : 2026-10-08

## Résumé

Un connecteur partagé se décrit dans un fichier YAML, `connectors/<nom>/connector.yaml`, validé par `connectors/connector.schema.json` (JSON Schema 2020-12). L'unité du format est la fonction servie avec son appel : ce que l'agent voit (nom, description en anglais, schéma d'entrée strict, classe, exemples, refus nommés) et la requête derrière (méthode, chemin, répartition des arguments, pagination, sortie). Ce qui ne se décrit pas reste du code écrit à la main, référencé depuis le fichier. Le bloc `connector` dit aussi ce qu'est un compte : les champs de son credential, ses réglages non secrets (région, adresse) et son authentification, consentement OAuth d'une personne compris. La [fabrique](fabrique.md) en tire Python et TypeScript ; quatorze connecteurs ont leur description, et tout client ajouté à la lib entre au format.

## Contexte

- La lib porte une centaine de clients Python écrits à la main. Au relevé du 29/09, le schéma d'un outil y vient de la signature et sa description de la docstring ; aucune classe n'est déclarée, aucun refus n'est nommé, la confirmation se fait au cas par cas.
- Le paquet d'oto 2 sert des fonctions qui déclarent classe, exemples, refus et résumé de confirmation. Elles ne sont pas des outils MCP : elles se trouvent, se lisent et s'appellent par les six outils figés du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Un connecteur partagé sert deux langages : Python pour la lib, TypeScript pour le paquet. Écrit deux fois à la main, il divergerait ; d'où une description unique.
- Relevé du 29/09 sur 114 connecteurs de la lib, par lecture du code et à seuil arbitraire : 54 enveloppes d'API REST, 44 REST avec logique, 16 hors format (3 navigateur, 7 données ouvertes ou base embarquée, 3 SDK, 2 traitement local, 1 asynchrone).
- Se décrit : authentification (clé, basique, client credentials, consentement d'une personne), réglages d'un compte, URL de base, verbe et chemin, répartition des arguments, constantes et en-têtes constants, encodage JSON d'un argument, schéma, table des erreurs et leur caractère rejouable, contrôles simples avant l'appel et sur la réponse, projection de sortie, règle de coût, pagination, rythme maximal du tiers, sonde. Reste à la main : fusion de plusieurs appels, refus locaux que les contrôles déclarés ne disent pas, construction d'un mail, identité opérée.

## Objectifs et non-objectifs

- Une seule source par connecteur partagé, d'où sortent les deux langages.
- Le contrat vu de l'agent s'écrit une fois : classe, exemples validés, refus nommés, en anglais.
- Un fichier invalide est refusé avant toute génération.
- Ce qui ne se décrit pas reste possible, par une entrée `handwritten` dont le schéma, la classe et les refus restent déclaratifs.
- Hors objectif : convertir les connecteurs existants. Ils restent en Python tels quels ; seuls les nouveaux connecteurs partagés et ceux qu'oto 2 demande entrent au format, et un existant réclamé entre comme un nouveau.
- Hors objectif : décrire un connecteur propre à un hôte, écrit dans l'hôte au contrat du paquet (oto-pkg : docs/conception/connecteurs-et-comptes.md).
- Hors objectif : porter un secret, un compte ou une activation. Le fichier nomme les champs du credential et les réglages, et décrit le consentement ; garder, chiffrer, rafraîchir et résoudre relèvent de l'hôte.

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
| `base_url` | sauf `base_urls` ou tout `handwritten` | Racine `https://` des chemins d'appel, sans `/` final ; gabarit possible sur les réglages. |
| `base_urls` | non, exclusif de `base_url` | Une racine par valeur d'un réglage `choice` : `{ setting: region, values: { us: …, eu: … } }`. |
| `auth` | oui | Mode d'authentification, voir « Le compte ». |
| `credential` | sauf `none` et `oauth2_user` | Champs fournis par le consommateur : `name`, `label`, `secret` (booléen). |
| `settings` | non | Réglages non secrets d'un compte, voir « Le compte ». |
| `modes` | oui | Qui peut porter un compte : `platform`, `byo_user`, `byo_org`. |
| `timeout_s` | non (30) | Délai d'un appel, en secondes. |
| `quota` | non | Quota par défaut sur une clé de la plateforme ; n'existe que si `modes` contient `platform`. |
| `query_arrays` | non | Encodage des listes en query : `repeat`, `brackets`, `comma`. |
| `headers` | non | En-têtes constants de toute requête : `{ Notion-Version: "2025-09-03" }`. Jamais un secret ni l'en-tête d'authentification. |
| `rate_limit` | non | Rythme maximal du tiers, par credential : `{ requests: 4, seconds: 1 }`. |
| `probe` | non | Sonde de la connexion : `{ function: get_company, non_empty: [scopes] }`, une lecture sans argument requis, jamais décomptée d'un quota ; chaque chemin de `non_empty` doit être non vide dans sa réponse. |
| `errors` | oui | Table commune à toutes les fonctions : `status` (un ou plusieurs codes HTTP), `code` (refus nommé), `message` (anglais), `retryable`. |

### Le compte

Un compte garde tous les champs que déclare `credential` (les secrets chiffrés par l'hôte) et ses réglages, `settings` : `name`, `label`, `type`, et selon le type, `choice` (`choices`, liste fermée, `default` facultatif : `{ name: region, label: Data region, type: choice, choices: [us, eu], default: us }`), `text` (`pattern`, expression ancrée : `{ name: domain, label: Domain, type: text, pattern: "^[a-z0-9-]+$" }`) ou `url` (adresse `https://` libre, saisie par un admin : serveur d'un standard, site).

Un réglage n'est cité que par une adresse. `base_url`, `token_url` et `authorize_url` sont des gabarits : `https://{domain}.example.net` (domaine d'un tenant), `{server}/xapi/v1`. Un réglage `url` (ou une valeur de `from_token`) ne se cite qu'en tête, à la place du schéma et de l'hôte ; un autre réglage, qu'après `https://`. `base_urls` et `token_urls` donnent une adresse à chaque valeur d'un `choice`, quand les hôtes ne se déduisent pas l'un de l'autre. L'exécution garde ces adresses : `https` seul, jamais un hôte qui résout vers une adresse interne, aucune redirection suivie ; une valeur `text` ou `choice` ne porte que lettres, chiffres, `-` et `_`.

`auth.kind`, chaque secret nommant un champ de `credential`, jamais une valeur :
- `api_key` : `in` (`header` ou `query`), `name` (de l'en-tête ou du paramètre), `key`, `prefix` en en-tête seulement : `{ kind: api_key, in: header, name: X-Claap-Key, key: api_key }` ;
- `bearer` (`token`) ; `basic` (`username`, `password`, deux champs) ;
- `oauth2_client_credentials` : `token_url` ou `token_urls`, `token_request` (`json`, `form`), `client_auth` (`body` ou `basic` : où vont l'identifiant et le secret du client), `client_id`, `client_secret`, `scope` et `expires_in_default` facultatifs ;
- `oauth2_user`, le consentement d'une personne (code d'autorisation) : `authorize_url`, `token_url` ou `token_urls` (corps en formulaire), `client_auth`, `scopes`, `authorize_params` (constantes, jamais un paramètre que pose l'hôte : `client_id`, `redirect_uri`, `state`…), `pkce: S256`, `refresh` (`refresh_token` avec `rotates` booléen, `exchange` avec `exchange: { grant_type, token_param }`, ou `none`), `expires_in_default`, `identity: { function: get_me, path: userPrincipalName }` (qui a consenti, pour nommer le compte : une lecture sans argument requis), `from_token: [instance_url]` (champs de la réponse de jeton gardés avec le compte, citables par `base_url`) ;
- `none`.

En `oauth2_user`, `credential` est interdit : les jetons du consentement ne sont pas des champs, et `client_id`/`client_secret` appartiennent à l'application OAuth (celle de l'hôte par défaut, celle de l'organisation si elle en pose une), pas au compte ni au fichier.

### Le bloc `functions`

| Champ | Obligatoire | Sens |
|---|---|---|
| `name` | oui | Un verbe et son objet : `list_estimates`. |
| `class` | oui | `read`, `write` ou `sensitive`. |
| `description` | oui | En anglais : ce que l'agent lit. |
| `input` | oui | JSON Schema 2020-12 d'un objet strict (`type: object`, `additionalProperties: false`, `properties`), servi et validé tel quel. |
| `examples` | oui, au moins un | `title` et `input`, chaque `input` validé contre `input`. |
| `refusals` | non | `code`, `when` (statut HTTP ou condition), `message` : surcharge ou complète la table commune. |
| `checks` | non | Contrôles de l'entrée avant l'appel, chacun nomme un refus : `{ kind: equal_sums, refusal: entry_unbalanced, items: ledger_entry_lines, fields: [debit, credit] }` (sommes égales, chaînes décimales sommées exactement). |
| `expect` | non | Contrôles de la réponse, chacun nomme un refus : `{ kind: non_empty, refusal: pdf_missing, path: public_file_url }`. |
| `call` | sauf `handwritten` | `method`, `path` relatif avec `{param}`, puis `query`, `body` (envoyé quelle que soit la méthode, `DELETE` compris), `headers` qui associent un nom côté API à un argument ; `constants` (`query`, `body`, `headers`) : des valeurs envoyées à chaque appel, hors du schéma, comme `{ body: { draft: true } }` ; `encode` : `{ filter: json }` sérialise un argument structuré en une chaîne. Chaque argument va à un seul endroit, chaque nom côté API reçoit une seule valeur. |
| `handwritten` | exclusif de `call` | `python` (`module`, `function`) et `typescript` (`file`, `export`). |
| `pagination` | non | `kind` (`cursor` ou `page`), `request_param`, `next`, `more` facultatif (booléen de la réponse, `false` à la dernière page : `has_more`), `total` facultatif, `max_pages`. Présente, elle ajoute `all_pages` et `max_pages` au schéma servi. |
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
    description: Get the full record of one Sellsy estimate by its id.
    input: { type: object, additionalProperties: false, required: [id], properties: { id: { type: integer } } }
    examples: [{ title: One estimate with its company, input: { id: 42, embed: [company] } }]
    refusals:
      - { code: estimate_not_found, when: 404, message: "No estimate with this id in this Sellsy account." }
    call: { method: GET, path: "/estimates/{id}", query: { field: fields, embed: embed } }
```

(Extrait raccourci : le fichier déclare aussi `fields` et `embed` dans `input`.)

### Les connecteurs décrits

Quatorze connecteurs ont leur `connector.yaml` (nombre de fonctions) : `affinity` (33), `aircall` (18), `amplitude` (16), `claap` (6), `mailpool` (21), `meta_ads` (8), `microsoft` (13), `nextmotion` (139), `notion` (22), `pennylane` (48), `pennylane_firm` (7), `sellsy` (2), `typeform` (4), `wttj_ats` (10). Chaque valeur vient du client Python ou de la référence publique de l'éditeur ; quand les deux divergent, le fichier suit le client. Une fonction que le format ne sait pas dire reste hors du fichier plutôt que d'y être approchée : ainsi `mailpool.update_domain_dns`, dont le corps est un tableau d'enregistrements.

`pennylane` (API v2, une clé par société) couvre ce que couvre le client : référentiels, clients, fournisseurs, factures clients et avoirs, devis, factures d'achat (lecture, correction, validation), grand livre et lettrage, transactions, balance, rapprochement ; 26 lectures, 13 écritures, 9 fonctions sensibles. Valeurs vérifiées le 2026-10-06 contre l'OpenAPI publique « Company V2 ». Restent hors du fichier : le téléversement de pièce (multipart) et l'import de facture d'achat qui en dépend, les deux recherches anti-doublon par `external_reference` du client (servies par le `filter` des listes), l'agrégat `fetch_complete_data` (plusieurs appels) et l'option `only_outstanding` des transactions (filtre local). Le `filter` est une liste de clauses `{field, operator, value}` (champs énumérés par fonction, liste exigée pour `in` et `not_in`), sérialisée en JSON dans la query par `encode` ; `draft: true` est une constante de corps ; le rythme (4 requêtes par seconde), l'arrêt sur `has_more`, la sonde (`get_company`, `scopes` non vide), l'équilibre d'une écriture et le lien PDF d'un devis sont déclarés. Un avoir prend des quantités négatives, imposées par le schéma, là où le client inverse le signe.

`pennylane_firm` (API Firm v1, un jeton de cabinet pour toutes les sociétés du cabinet, détenu par l'organisation) décrit les sociétés du cabinet, leurs exercices et la GED : dossiers, fichiers, journal des changements de fichiers, création de dossier ; 6 lectures, 1 écriture. Valeurs vérifiées le 2026-10-07 contre la référence publique « Firm API v1 ». Chaque fonction sauf la liste des sociétés prend `company_id`, l'identifiant de la société côté cabinet, dans son chemin. La GED n'a ni lecture unitaire, ni modification, ni suppression, ni déplacement. Reste hors du fichier le dépôt d'un fichier dans la GED : le format ne connaît pas le corps multipart. Le dépôt est servi par le client Python (`upload_dms_file`, corps envoyé en flux, le fichier lu par morceaux bornés) derrière un relais propre à l'hôte. Les sociétés et les exercices se paginent par numéro de page sans `pagination` déclarée (voir « Questions ouvertes ») ; le journal des changements, sans `pagination` non plus, car `start_date` et `cursor` s'excluent.

Comptes, au 2026-10-08 : `typeform` (1.1.0 ; `us`, `eu`, `eu2`, comme le client) et `amplitude` (1.1.0 ; `us`, `eu`) choisissent leur hôte par un réglage `region` ; `sellsy` (1.0.1) dit `client_auth: body` ; `microsoft` (3.0.0) passe de `bearer` à `oauth2_user` (accès délégué, refresh token tourné, identité par `get_me`) ; `claap` et `mailpool` (1.0.1) disent `in: header` ; `aircall` (`basic` sur deux champs) ne change pas. `meta_ads` reste en `bearer`, jeton fourni par l'hôte : son dialogue prend un `config_id` propre à l'application et aucun scope, et l'identité se lit par `/me` avec un repli selon le type de jeton ; le format ne dit ni un paramètre d'application autre que l'identifiant et le secret du client, ni ce repli.

Quatre clients n'ont pas de fichier, aucune de leurs fonctions n'étant descriptible : `threecx` (l'adresse du standard se dit, mais la connexion par compte utilisateur est hors OAuth2 standard ; audio binaire), `boondmanager` (jeton signé à chaque requête), `bigquery` (SDK), `wordpress` (l'adresse du site se dit, mais la racine REST se découvre à l'appel, `/wp-json/` ou `?rest_route=` ; téléversement de média en corps binaire).

### Contrôles

`tests/test_connector_descriptions.py` vérifie que le schéma est un JSON Schema valide, que chaque description le respecte et porte le nom de son dossier, et que chaque exemple respecte l'entrée de sa fonction. Quarante et une variantes invalides du fichier Sellsy doivent être refusées : classe absente, clé inconnue, `call` et `handwritten` ensemble ou aucun des deux, fonction sensible sans `confirm`, entrée non stricte, aucun exemple, exemple hors bornes ou à argument inconnu, `embed` non documenté, `token_url` absent, `credential` absent, `quota` sans `platform`, version hors semver, `exposure.mode` inconnu, schéma de sortie ouvert ou sans propriétés, `rate_limit` incomplet, en-tête constant non textuel, encodage inconnu, contrôle d'un genre inconnu, attente sans chemin, sonde à clé inconnue, `more` vide ; et pour le compte : `client_auth` absent, `token_url` et `token_urls` ensemble, `base_url` et `base_urls` ensemble, liste fermée d'une valeur, `text` sans motif ou à motif non ancré, `url` à liste, gabarit à `/` final, clé en query préfixée, clé sans place, `oauth2_user` avec un credential, avec `client_id`, sans identité, `rotates` hors `refresh_token`, `exchange` sans sa demande, paramètre d'autorisation réservé à l'hôte, PKCE inconnu. Les règles hors schéma sont vérifiées par la fabrique ([fabrique](fabrique.md)). `jsonschema` est un outil de test, pas une dépendance de la lib.

## Décisions et alternatives écartées

- **Du code source dans l'un des deux langages** : écarté le 29/09. L'autre langage n'en serait qu'une traduction ; un fichier déclaratif se lit des deux côtés.
- **TOML** : écarté, illisible dès trois niveaux d'imbrication, qu'un schéma d'entrée dépasse souvent. **JSON seul** : écarté, sans commentaires et pénible pour une description sur plusieurs lignes.
- **Référencer la spec OpenAPI de l'éditeur** (`operation: getEstimates`) et n'ajouter que classe, refus, exemples et projection : non retenu à ce jour. Moins à écrire, mais chaque connecteur dépendrait de la qualité d'une spec tierce.
- **Un client ajouté à la lib sans description** : écarté le 2026-10-05. Tout client entre au format ; une fonction ou un client que le format ne sait pas dire est nommé, avec sa raison, dans « Les connecteurs décrits ».
- **L'outil MCP comme unité** (le modèle d'oto 1, où le nom de l'outil est le contrat et où Sellsy est servi par `sellsy_document(kind, op)`, quatre documents de vente fusionnés) : écarté. L'unité est la fonction ; l'outil, une projection. Cela répond au point « verbe `oto_call` ou outils dédiés » d'ADR 0070 §7.8 : les deux, par projection.
- **Un langage d'expressions pour les contrôles** (une formule « débits = crédits ») : écarté le 06/10. Des genres fermés et nommés (`equal_sums`, `non_empty`), chacun lié à un refus de la table, se lisent et s'exécutent pareil dans les deux langages ; un genre s'ajoute quand une description en a besoin.
- **Refus en texte libre** : écarté. Une table structurée (`code`, `when`, `message`) que la fabrique traduit en erreurs typées dans les deux langages.
- **Une seule version** : écarté. La version du fichier (semver) et celle de l'API tierce (en clair) sont deux champs.
- **Convertir les 713 outils existants, ou leur attribuer une classe** : écarté. La classe s'écrit à l'entrée d'un connecteur dans le format.
- **Garder les descriptions en français** : écarté. Ce que lit l'agent est en anglais ; une description française se traduit à son entrée.
- **Un seul secret par compte, l'adresse ou la région dans un champ du credential** : écarté le 2026-10-08. Un compte garde tous ses champs ; une région ou une adresse est un réglage non secret, typé (liste fermée, motif, adresse libre gardée à l'exécution), cité par un gabarit, que l'écran de l'hôte propose et que l'exécution vérifie.
- **Un connecteur par région** (`typeform_eu`) : écarté le 2026-10-08 ; les fonctions et le contrat ne changent pas avec l'hôte.
- **`oauth2_user` renvoyé à du code écrit à la main, le jeton passé en `bearer`** : écarté le 2026-10-08. Le consentement (adresses, scopes, PKCE, renouvellement, rotation, identité) se décrit, et l'hôte l'exécute une fois pour tous.
- **L'identifiant et le secret du client OAuth dans `credential`** : écarté le 2026-10-08. Ils appartiennent à l'application (de l'hôte, ou de l'organisation qui pose la sienne), pas au compte d'une personne.

## Sécurité et confidentialité

- Le fichier ne porte aucun secret : il nomme les champs du credential (`secret: true`) et `auth` y renvoie par leur nom ; un réglage n'est jamais un secret. Une clé passée en query (`in: query`) est admise : le tiers l'a voulue là et la voit de toute façon ; l'exécution la masque dans tout journal, adresse comprise. Le secret est fourni par le consommateur à chaque appel ; la lib n'en lit aucun (garde `tests/test_no_secret_read_guard.py`).
- Une adresse saisie par un admin (`url`, valeur de `from_token`) n'est suivie qu'en `https`, vers un hôte qui ne résout jamais vers une adresse interne, sans redirection ; le format le déclare, l'exécution le garde.
- `base_url` nomme la racine documentée de l'API de l'éditeur. Une coordonnée tirée du front d'un tiers (clé d'API publique, identifiant d'application) n'entre jamais dans un fichier (`docs/conventions.md`).
- Le dépôt est public : un connecteur dont l'accès appartient à un client, son propre back-office, n'a pas de description ici ; c'est un connecteur propre à son hôte.
- Un message de refus dit le fait, au plus une condition, jamais le nom d'un outil : la lib ne connaît pas les outils de l'appelant.

## Écart avec le code

- Écrits : le schéma, son test et quatorze descriptions (« Les connecteurs décrits ») ; le schéma et Sellsy depuis la version 1.149.0 de la lib, dix autres depuis le 2026-10-05, `mailpool` ensuite, `pennylane` le 2026-10-06, `pennylane_firm` le 2026-10-07, sans client Python ; le client `oto.tools.pennylane_firm` a suivi le 2026-10-08, écrit d'après la description (mêmes noms de méthode, plus `upload_dms_file`). Aucun test n'impose encore qu'un client ait sa description, et le client `wordpress`, ajouté en 1.156.0, n'en a pas.
- Les descriptions ne partent pas dans la distribution PyPI : ni la roue ni l'archive source de la 1.154.0 ne les contiennent. Seul le dépôt les porte.
- `modes` garde le vocabulaire d'oto 1 (`platform`, `byo_user`, `byo_org`).
- `quota` n'a pas de forme : le schéma n'exige qu'un objet non vide.
- Le format se déclare YAML 1.2, mais le test lit avec PyYAML, qui suit YAML 1.1.
- Les clients Python restent écrits à la main, celui de Sellsy sur des verbes génériques, et rien n'est généré ; leurs descriptions, écrites après eux (sauf `pennylane_firm`, dont le client a suivi la description), n'en sont pas encore la source.
- `pennylane` s'écarte du client là où le client lit une seule page d'une liste paginée (exercices, catégories, lignes de facture et de devis) : le fichier les décrit paginées, comme l'OpenAPI. Un `DELETE` y porte un corps (délettrage) : la sortie le porte dans la requête, l'hôte l'envoie.
- Les ajouts du 2026-10-08 (réglages, gabarits, `base_urls`, clé en query, `client_auth`, `oauth2_user` déclaratif) n'ont pas d'exécution : le paquet d'oto 2 ne sait encore ni réglage, ni consentement, ni rotation. `in: query`, `token_urls`, `from_token`, `pkce` et `exchange` ne servent à aucune description.
- Les clients Python `typeform` et `amplitude` prennent déjà leur région en paramètre ; `microsoft` garde son module de consentement écrit à la main (`oto/tools/microsoft/auth.py`), que la description recopie.
- Les ajouts du 2026-10-06 (en-têtes constants, constantes, `encode`, `more`, `rate_limit`, `probe`, `checks`, `expect`) ne servent qu'à `notion`, `pennylane` et `pennylane_firm` ; les autres descriptions ne s'en servent pas encore.

## Questions ouvertes

- La forme de `quota`.
- Le vocabulaire de `modes` : organisation, équipe et personne, ou le retirer du fichier, puisque le compte relève de l'hôte ?
- YAML 1.2 déclaré, lecteur 1.1 : changer de lecteur, ou se limiter au sous-ensemble commun et le dire ?
- Référencer la spec OpenAPI de l'éditeur quand elle existe ?
- Le défaut `per_connector` vaut-il pour un hôte d'oto 2, dont les fonctions ne passent que par `call` ?
- Ce que le format ne sait pas encore dire, relevé en décrivant dix connecteurs le 2026-10-05 :
  - un corps qui est un tableau (`affinity`, `nextmotion`, `microsoft`), un corps en formulaire (`meta_ads`) ;
  - une réponse autre que JSON (CSV, fichier binaire), une pagination par adresse complète (`@odata.nextLink`) ;
  - une signature par requête (`boondmanager`) ;
  - `handwritten` exige un fichier TypeScript, qui n'existe pour aucun connecteur ;
  - un coût par élément d'un lot, ou relu dans la réponse de l'amont : `cost` ne connaît que `per: request` et `per: page`, alors qu'un lot payant se facture au contact soumis, ou seulement à la donnée trouvée, chiffrée dans la réponse ;
  - ce que couvre une sonde au-delà de l'authentification et des champs non vides : le quota restant ?
- Relevé en décrivant `pennylane` le 2026-10-06 : des clauses de filtre bâties depuis des arguments nommés (bornes de date, statut, `external_reference`), plutôt qu'une liste de clauses écrite par l'agent.
- Relevé en décrivant `pennylane_firm` le 2026-10-07 : un corps multipart ; une pagination par numéro de page dont la réponse dit la page courante et le nombre de pages, pas la suivante (`current_page`, `total_pages`) ; un argument qui ne part plus une fois le curseur posé (`start_date` du journal des changements).
- Un test doit-il refuser un client sans description, avec une liste nommée d'exceptions ?
- Relevé le 2026-10-08 : un réglage cité ailleurs que dans une adresse (en-tête qui choisit la société) ; l'identité lue dans la réponse de jeton plutôt que par une fonction ; un paramètre d'application autre que le client (`config_id`) ; des scopes séparés par des virgules.

## Historique

- 2026-09-29 : le connecteur partagé se décrit dans un fichier déclaratif ; l'unité est la fonction servie avec son appel ; l'exposition est une projection ; les descriptions sont en anglais — décidé par le mainteneur (source : séance du 29/09 ; conception connecteurs d'oto 2, oto-enterprise, archivé).
- 2026-09-29 : YAML validé par un schéma JSON ; trois blocs ; refus en table structurée ; version du fichier et version de l'API séparées ; projection par défaut `per_connector` ; existants non convertis, Sellsy d'abord — décidé par le mainteneur (source : séance du 29/09, « Le fichier de description »).
- 2026-09-30 : schéma `connector.schema.json` et fichier Sellsy vérifié contre la référence publique de l'API ; ajouts : le champ de `auth` qui nomme le secret (`key`, `token`, `username`, `password`), `scope` en OAuth2, l'erreur 400 `invalid_request` — choix du projet (source : lot 2 du 30/09).
- 2026-10-05 : reprise en document de conception vivant depuis la conception connecteurs d'oto 2 (oto-enterprise, archivé) — décidé par le mainteneur.
- 2026-10-05 : tout client ajouté à la lib entre au format ; dix connecteurs décrits, trois non descriptibles en l'état, et la liste de ce que le format ne sait pas encore dire — décidé par le mainteneur (source : séance du 05/10).
- 2026-10-06 : description `pennylane`, 48 fonctions, et cinq trous du format relevés — choix du projet.
- 2026-10-05 : la description microsoft passe à l'accès délégué (jeton d'une personne, fourni par l'hôte), version 2.0.0 — choix du projet (source : refonte du client, v1.155.0).
- 2026-10-06 : le format dit en-têtes constants, constantes de requête, encodage JSON d'un argument, arrêt de pagination (`more`), rythme maximal, sonde, contrôles avant l'appel (`checks`) et sur la réponse (`expect`) ; `notion` et `pennylane` (2.0.0) s'en servent ; l'entrée est servie en JSON Schema tel quel ; `nextmotion` (1.0.1) corrigé : un `oneOf` aux branches qui se recouvrent devient un `anyOf` de types disjoints (six fonctions), et onze branches `enum: []`, qui n'acceptaient rien, sont retirées — décidé par le mainteneur.
- 2026-10-07 : description `pennylane_firm`, 7 fonctions ; le dépôt de fichier dans la GED reste hors du fichier, et trois trous du format relevés — choix du projet.
- 2026-10-08 : un compte garde tous les champs de son credential et des réglages non secrets (liste fermée, motif, adresse libre gardée à l'exécution) ; `base_url` et `token_url` dépendent d'un réglage (`base_urls`, `token_urls` ou gabarit) ; clé admise en query, masquée à l'exécution ; `client_auth` ; `oauth2_user` déclaratif, sans credential, l'application OAuth hors du compte ; `typeform`, `amplitude`, `sellsy`, `microsoft` (3.0.0) passés au format, `meta_ads` non — décidé par le mainteneur.
- 2026-10-08 : client Python `pennylane_firm` (les 7 fonctions, plus le dépôt multipart en flux, 5 requêtes par seconde par jeton) ; le dépôt de fichier reste hors de la description, faute de corps multipart dans le format, et passe par un relais propre à l'hôte au lieu d'aller du poste à Pennylane — décidé par le mainteneur.
