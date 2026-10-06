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

/** Un champ du credential que l'hôte fournit à l'appel. */
export type CredentialField = { readonly name: string; readonly label: string; readonly secret: boolean }

/** Chaque valeur qui désigne un secret nomme un champ de `credential`, jamais une valeur. */
export type Auth =
  | { readonly kind: "api_key"; readonly header: string; readonly prefix?: string; readonly key: string }
  | { readonly kind: "bearer"; readonly token: string }
  | { readonly kind: "basic"; readonly username: string; readonly password: string }
  | {
      readonly kind: "oauth2_client_credentials"
      readonly tokenUrl: string
      readonly tokenRequest: "json" | "form"
      readonly clientId: string
      readonly clientSecret: string
      readonly scope?: string
      readonly expiresInDefault?: number
    }
  | { readonly kind: "oauth2_user"; readonly handwritten: { readonly file: string; readonly export: string } }
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
  /** Absente quand toutes les fonctions sont écrites à la main. */
  readonly baseUrl?: string
  readonly auth: Auth
  readonly credential: readonly CredentialField[]
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
