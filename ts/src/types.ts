// Le contrat des définitions que la fabrique génère depuis `connectors/*/connector.yaml` (écrit à la main, pas généré).
// Des données structurelles seulement : l'hôte adapte chaque définition à son propre contrat de fonction et exécute la
// requête avec son client HTTP, son coffre et son journal. Rien ici n'envoie de requête ni ne lit de secret.

/** read : lecture ; write : écriture ; sensitive : envoie, supprime ou paie, confirmé avant exécution. */
export type FunctionClass = "read" | "write" | "sensitive"

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE"

/** Un JSON Schema (2020-12), porté tel quel : l'hôte le valide avec un validateur JSON Schema standard. */
export type JsonSchema = { readonly [key: string]: unknown }

/** Valeur JSON, telle qu'une constante la porte. */
export type JsonValue = string | number | boolean | null | readonly JsonValue[] | { readonly [key: string]: JsonValue }

/** Un champ du credential que l'hôte fournit à l'appel ; un compte garde tous les champs que déclare son connecteur. */
export type CredentialField = { readonly name: string; readonly label: string; readonly secret: boolean }

/**
 * Un réglage non secret d'un compte, saisi par qui le connecte. `choice` : une valeur d'une liste fermée (une région),
 * `default` à défaut. `text` : un texte libre qui satisfait `pattern` (le sous-domaine d'un tenant). `url` : une adresse
 * `https://` libre, saisie par un admin. Un réglage n'est cité que par une adresse (`baseUrl`, `baseUrls`, `tokenUrl`,
 * `tokenUrls`, `authorizeUrl`).
 *
 * L'hôte garde l'exécution : une adresse libre (réglage `url` ou valeur de `fromToken` en tête d'un gabarit) est en
 * `https` seulement, son hôte ne résout jamais vers une adresse interne (boucle locale, plages privées, lien-local), à
 * chaque résolution, et aucune redirection n'est suivie ; un réglage `text` ou `choice` ne se substitue que s'il ne
 * porte que des lettres, des chiffres, `-` et `_`.
 */
export type Setting =
  | { readonly name: string; readonly label: string; readonly type: "choice"; readonly choices: readonly string[]; readonly default?: string }
  | { readonly name: string; readonly label: string; readonly type: "text"; readonly pattern: string }
  | { readonly name: string; readonly label: string; readonly type: "url" }

/** Une adresse par valeur d'un réglage `choice` : chaque valeur a la sienne. */
export type UrlsBySetting = { readonly setting: string; readonly values: { readonly [choice: string]: string } }

/**
 * Comment l'identifiant et le secret du client atteignent le point de jeton : dans le corps de la requête, ou en HTTP
 * Basic.
 */
export type ClientAuth = "body" | "basic"

/** Le point de jeton : une adresse (gabarit possible sur les réglages) ou une adresse par valeur d'une liste fermée. */
export type TokenEndpoint = { readonly tokenUrl: string; readonly tokenUrls?: never } | { readonly tokenUrls: UrlsBySetting; readonly tokenUrl?: never }

/**
 * Le renouvellement d'un consentement. `refresh_token` : la demande par jeton de rafraîchissement ; si `rotates`, chaque
 * réponse porte un nouveau jeton de rafraîchissement qui remplace l'ancien, que l'hôte réécrit seulement si le jeton
 * stocké est encore celui qu'il a lu. `exchange` : le jeton d'accès courant, envoyé dans `exchange.tokenParam` avec
 * `grant_type` = `exchange.grantType`, contre un nouveau. `none` : le jeton vaut jusqu'à expiration ou révocation, puis
 * la personne consent de nouveau.
 */
export type OAuthRefresh =
  | { readonly refresh: "refresh_token"; readonly rotates: boolean; readonly exchange?: never }
  | { readonly refresh: "exchange"; readonly exchange: { readonly grantType: string; readonly tokenParam: string }; readonly rotates?: never }
  | { readonly refresh: "none"; readonly rotates?: never; readonly exchange?: never }

/**
 * Le consentement d'une personne (code d'autorisation, OAuth 2.0). L'application OAuth (identifiant et secret du client)
 * n'est ni dans la description ni dans `credential` : c'est celle de l'hôte, ou celle que pose l'organisation. Les jetons
 * qu'il rend ne sont pas des champs de `credential`. Le point de jeton reçoit un corps en formulaire ; les `scopes` se
 * joignent par des espaces ; `authorizeParams` s'ajoutent à l'adresse d'autorisation, jamais un paramètre que l'hôte
 * pose lui-même (`client_id`, `redirect_uri`, `response_type`, `scope`, `state`, `code_challenge*`).
 */
export type OAuthUser = {
  readonly kind: "oauth2_user"
  readonly authorizeUrl: string
  readonly clientAuth: ClientAuth
  readonly scopes?: readonly string[]
  readonly authorizeParams?: { readonly [param: string]: string }
  /** Preuve de possession du code (PKCE), par SHA-256. */
  readonly pkce?: "S256"
  /** Durée de vie, en secondes, d'un jeton d'accès dont la réponse n'en donne pas. */
  readonly expiresInDefault?: number
  /**
   * Qui a consenti, lu juste après le consentement pour nommer le compte : `function` (nom qualifié, une lecture sans
   * argument requis), appelée avec `{}`, et le chemin pointé d'une valeur non vide de sa réponse (l'adresse de la personne).
   */
  readonly identity: { readonly function: string; readonly path: string }
  /**
   * Champs de la réponse de jeton gardés avec le compte, non secrets, renouvelés à chaque réponse de jeton, cités par
   * `baseUrl` comme un réglage (une `instance_url` qui devient l'adresse de l'API).
   */
  readonly fromToken?: readonly string[]
} & TokenEndpoint & OAuthRefresh

/**
 * Chaque valeur qui désigne un secret nomme un champ de `credential`, jamais une valeur. Une clé passée en query
 * (`in: "query"`) est masquée par l'hôte dans tout journal, adresse comprise.
 */
export type Auth =
  | { readonly kind: "api_key"; readonly in: "header"; readonly name: string; readonly prefix?: string; readonly key: string }
  | { readonly kind: "api_key"; readonly in: "query"; readonly name: string; readonly prefix?: never; readonly key: string }
  | { readonly kind: "bearer"; readonly token: string }
  | { readonly kind: "basic"; readonly username: string; readonly password: string }
  | ({
      readonly kind: "oauth2_client_credentials"
      readonly tokenRequest: "json" | "form"
      readonly clientAuth: ClientAuth
      readonly clientId: string
      readonly clientSecret: string
      readonly scope?: string
      readonly expiresInDefault?: number
    } & TokenEndpoint)
  | OAuthUser
  | { readonly kind: "none" }

/** Une ligne de la table d'erreurs du connecteur : un ou plusieurs statuts HTTP du tiers, le refus nommé. */
export type ApiError = {
  readonly status: number | readonly number[]
  readonly code: string
  readonly message: string
  readonly retryable: boolean
}

/** Un refus propre à une fonction : surcharge ou complète la table d'erreurs ; `when` est un statut HTTP ou une condition. */
export type Refusal = { readonly code: string; readonly when: number | string; readonly message: string }

/** Un exemple d'arguments, valide contre le schéma de sa fonction (vérifié par la fabrique et par un test). */
export type Example = { readonly title: string; readonly input: { readonly [argument: string]: unknown } }

/** Nom côté API → nom de l'argument d'entrée. Chaque argument va à un seul endroit de la requête. */
export type ArgumentMap = { readonly [apiName: string]: string }

/** En-tête → valeur constante ; jamais un secret. */
export type ConstantHeaders = { readonly [header: string]: string }

export type RequestSpec = {
  readonly method: HttpMethod
  /** Relatif à `baseUrl`, avec des `{param}` qui nomment des arguments, listés dans `pathParams`. */
  readonly path: string
  readonly pathParams: readonly string[]
  /** Les listes s'y encodent selon `Connector.queryArrays`. */
  readonly query: ArgumentMap
  /** Corps JSON, quelle que soit la méthode : un `DELETE` peut en porter un. */
  readonly body: ArgumentMap
  readonly headers: ArgumentMap
  /**
   * Valeurs envoyées à chaque appel, sous leur nom côté API, en plus des arguments ; elles ne sont pas dans le schéma.
   * Un nom n'y est jamais aussi un nom d'argument de la même place.
   */
  readonly constants?: {
    readonly query?: { readonly [apiName: string]: string | number | boolean }
    readonly body?: { readonly [apiName: string]: JsonValue }
    readonly headers?: ConstantHeaders
  }
  /** Argument → encodage appliqué avant de le placer : `json` sérialise l'argument structuré en une chaîne (`JSON.stringify`). */
  readonly encode?: { readonly [argument: string]: "json" }
}

/**
 * Présente, elle a ajouté au schéma les arguments `all_pages` et `max_pages`, qui ne vont pas dans la requête : l'hôte
 * suit `next` (chemin pointé dans la réponse) vers `requestParam` tant que `all_pages` est vrai, `max_pages` pages au plus.
 * Il s'arrête quand `next` est absent ou nul, ou quand `more` est présent et que la réponse y porte `false`.
 */
export type Pagination = {
  readonly kind: "cursor" | "page"
  readonly requestParam: string
  readonly next: string
  /** Chemin pointé d'un booléen de la réponse : `false` dit que la page est la dernière, quoi que porte `next`. */
  readonly more?: string
  readonly total?: string
  readonly maxPages: number
}

export type Output = {
  /** Chemin pointé de la liste dans la réponse. */
  readonly items?: string
  readonly strip?: readonly string[]
  readonly projection?: readonly string[]
  /** Forme d'un enregistrement, après `items`, `strip` et `projection`. */
  readonly schema?: JsonSchema
}

/**
 * Contrôle des arguments avant l'appel ; s'il échoue, rien n'est envoyé et l'hôte rend le refus nommé (`refusal`, un code
 * de `refusals`). `equal_sums` : sur les éléments de la liste `items`, la somme du premier champ égale celle du second ;
 * une chaîne décimale se somme exactement, sans virgule flottante.
 */
export type Check = {
  readonly kind: "equal_sums"
  readonly refusal: string
  readonly items: string
  readonly fields: readonly [string, string]
}

/**
 * Contrôle de la réponse ; s'il échoue, l'hôte rend le refus nommé au lieu de la réponse. `non_empty` : la valeur au
 * chemin pointé `path` existe et n'est ni nulle, ni une chaîne, une liste ou un objet vides.
 */
export type Expectation = { readonly kind: "non_empty"; readonly refusal: string; readonly path: string }

export type ConnectorFunction = {
  /** `<connecteur>.<fonction>`. */
  readonly name: string
  readonly connector: string
  readonly class: FunctionClass
  /** Anglais : ce que lit l'agent. */
  readonly description: string
  /** JSON Schema 2020-12 de l'entrée, celui de la description : racine `type: object`, `additionalProperties: false`. */
  readonly schema: JsonSchema
  readonly examples: readonly Example[]
  readonly refusals: readonly Refusal[]
  readonly checks?: readonly Check[]
  readonly expect?: readonly Expectation[]
  readonly request: RequestSpec
  readonly pagination?: Pagination
  readonly output?: Output
  /** Unité décomptée du quota. */
  readonly cost?: { readonly per: "request" | "page" }
  /** Gabarit du résumé montré avant d'exécuter une fonction sensible, avec des `{param}`. */
  readonly confirm?: { readonly summary: string }
}

export type Connector = {
  readonly name: string
  readonly label: string
  readonly namespace: string
  /** Version du fichier de description, semver. */
  readonly version: string
  /** Version de l'API du tiers, telle que l'éditeur la nomme. */
  readonly apiVersion: string
  /**
   * Racine des chemins, gabarit possible sur les réglages (`https://{domain}.example.net`, `{server}`). Absente quand
   * `baseUrls` la remplace, ou quand toutes les fonctions sont écrites à la main.
   */
  readonly baseUrl?: string
  /** Racine des chemins par valeur d'un réglage `choice` (une région). */
  readonly baseUrls?: UrlsBySetting
  readonly auth: Auth
  /** Vide pour `oauth2_user` et `none`. */
  readonly credential: readonly CredentialField[]
  readonly settings?: readonly Setting[]
  readonly modes: readonly ("platform" | "byo_user" | "byo_org")[]
  readonly timeoutMs: number
  readonly queryArrays?: "repeat" | "brackets" | "comma"
  /** En-têtes constants de toute requête du connecteur ; jamais l'en-tête d'authentification. */
  readonly headers?: ConstantHeaders
  /** Débit maximal que le tiers accepte, par credential : `requests` requêtes par fenêtre de `intervalMs`. */
  readonly rateLimit?: { readonly requests: number; readonly intervalMs: number }
  readonly quota?: { readonly [key: string]: unknown }
  readonly errors: readonly ApiError[]
  /**
   * La sonde d'une connexion : `function` (nom qualifié, une lecture sans argument requis) appelée avec `{}`, jamais
   * décomptée d'un quota ; chaque chemin de `nonEmpty` doit être présent et non vide dans sa réponse.
   */
  readonly probe?: { readonly function: string; readonly nonEmpty: readonly string[] }
  readonly exposure?: { readonly mode?: "per_action" | "per_connector" | "via_call"; readonly tool?: string }
  /** Les fonctions générées ; une fonction que la fabrique ne sait pas traduire n'y est pas. */
  readonly functions: readonly ConnectorFunction[]
}

export function defineFunction<F extends ConnectorFunction>(definition: F): F {
  return definition
}

export function defineConnector<C extends Connector>(definition: C): C {
  return definition
}
