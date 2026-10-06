// Le contrat des définitions que la fabrique génère depuis `connectors/*/connector.yaml` (écrit à la main, pas généré).
// Des données structurelles seulement : l'hôte adapte chaque définition à son propre contrat de fonction et exécute la
// requête avec son client HTTP, son coffre et son journal. Rien ici n'envoie de requête ni ne lit de secret.
import type * as z from "zod/v4"

/** read : lecture ; write : écriture ; sensitive : envoie, supprime ou paie, confirmé avant exécution. */
export type FunctionClass = "read" | "write" | "sensitive"

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE"

/** Un JSON Schema, porté tel quel. */
export type JsonSchema = { readonly [key: string]: unknown }

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

export type Example<I> = { readonly title: string; readonly input: I }

/** Nom côté API → nom de l'argument d'entrée. Chaque argument va à un seul endroit de la requête. */
export type ArgumentMap = { readonly [apiName: string]: string }

export type RequestSpec = {
  readonly method: HttpMethod
  /** Relatif à `baseUrl`, avec des `{param}` qui nomment des arguments, listés dans `pathParams`. */
  readonly path: string
  readonly pathParams: readonly string[]
  /** Les listes s'y encodent selon `Connector.queryArrays`. */
  readonly query: ArgumentMap
  /** Corps JSON. */
  readonly body: ArgumentMap
  readonly headers: ArgumentMap
}

/**
 * Présente, elle a ajouté au schéma les arguments `all_pages` et `max_pages`, qui ne vont pas dans la requête : l'hôte
 * suit `next` (chemin pointé dans la réponse) vers `requestParam` tant que `all_pages` est vrai, `max_pages` pages au plus.
 */
export type Pagination = {
  readonly kind: "cursor" | "page"
  readonly requestParam: string
  readonly next: string
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

/** Un schéma d'arguments strict : une clé inconnue est refusée. */
export type StrictSchema = z.ZodObject<z.core.$ZodShape, z.core.$strict>

export type ConnectorFunction<S extends StrictSchema = StrictSchema> = {
  /** `<connecteur>.<fonction>`. */
  readonly name: string
  readonly connector: string
  readonly class: FunctionClass
  /** Anglais : ce que lit l'agent. */
  readonly description: string
  readonly schema: S
  readonly examples: readonly Example<z.input<S>>[]
  readonly refusals: readonly Refusal[]
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
  readonly quota?: { readonly [key: string]: unknown }
  readonly errors: readonly ApiError[]
  readonly exposure?: { readonly mode?: "per_action" | "per_connector" | "via_call"; readonly tool?: string }
  /** Les fonctions générées ; une fonction que la fabrique ne sait pas traduire n'y est pas. */
  readonly functions: readonly ConnectorFunction[]
}

/** Garde le type précis du schéma et vérifie les exemples contre lui, à la compilation. */
export function defineFunction<S extends StrictSchema>(definition: ConnectorFunction<S>): ConnectorFunction<S> {
  return definition
}

export function defineConnector<C extends Connector>(definition: C): C {
  return definition
}
