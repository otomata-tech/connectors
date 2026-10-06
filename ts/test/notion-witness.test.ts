// La définition générée de `notion.search_workspace` porte la même requête que le témoin écrit à la main dans le paquet
// d'oto 2 (oto-pkg : packages/plateforme/server/connectors/notion/search-workspace.ts). Les valeurs du témoin sont
// recopiées ici : ce dépôt ne dépend pas du paquet. `requestOf` montre qu'un adaptateur construit la requête à partir de
// la seule définition ; il n'est pas livré, l'exécution revient à l'hôte. L'en-tête constant `Notion-Version` du témoin
// est porté par le connecteur, l'entrée est validée par un validateur JSON Schema 2020-12 standard.
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { notion } from "../src"
import type { Connector, ConnectorFunction } from "../src/types"

const ajv = new Ajv2020({ strictTypes: false, strictTuples: false, validateFormats: false })

const witness = {
  baseUrl: "https://api.notion.com/v1",
  timeoutMs: 60_000,
  headers: { "Notion-Version": "2025-09-03" },
  method: "POST",
  path: "/search",
  bodyKeys: ["query", "filter", "sort", "start_cursor"],
  errors: [
    { status: 400, message: "Notion rejected the request as invalid: check the arguments." },
    { status: 401, message: "Notion rejected the integration token: check that it is valid." },
    { status: 403, message: "The integration lacks the capability for this action on this resource." },
    { status: 404, message: "Notion object not found, or not shared with the integration." },
    { status: 409, message: "Notion saw a conflicting edit: retry." },
    { status: 429, message: "Notion request rate exceeded: retry later." },
    { status: [500, 502, 503, 504], message: "Notion is temporarily unavailable: retry later." },
  ],
  examples: [
    { query: "roadmap", filter: { value: "page", property: "object" }, sort: { direction: "descending", timestamp: "last_edited_time" } },
    { query: "" },
  ],
}

/** La requête qu'un adaptateur enverrait : chemin rempli, en-têtes constants, query et corps tirés des arguments, clés absentes omises. */
function requestOf(connector: Connector, fn: ConnectorFunction, args: Record<string, unknown>) {
  const path = fn.request.pathParams.reduce((acc, param) => acc.replace(`{${param}}`, encodeURIComponent(String(args[param]))), fn.request.path)
  const pick = (map: Record<string, string>) =>
    Object.fromEntries(Object.entries(map).flatMap(([apiName, argument]) => (args[argument] === undefined ? [] : [[apiName, args[argument]]])))
  const query = new URLSearchParams(pick(fn.request.query) as Record<string, string>).toString()
  const body = pick(fn.request.body)
  return {
    url: `${connector.baseUrl}${path}${query ? `?${query}` : ""}`,
    method: fn.request.method,
    headers: { ...connector.headers, ...fn.request.constants?.headers, ...pick(fn.request.headers) },
    body: Object.keys(body).length > 0 || fn.request.method !== "GET" ? body : undefined,
  }
}

describe("notion.search_workspace against the hand-written witness", () => {
  const fn = notion.searchWorkspace
  const connector = notion.connector

  it("should carry the witness's API: base address, timeout, constant header, bearer token, error statuses and messages", () => {
    expect(connector.baseUrl).toBe(witness.baseUrl)
    expect(connector.timeoutMs).toBe(witness.timeoutMs)
    expect(connector.headers).toEqual(witness.headers)
    expect(connector.headers?.["Notion-Version"]).toBe(connector.apiVersion)
    expect(connector.auth).toEqual({ kind: "bearer", token: "token" })
    expect(connector.errors.map(({ status, message }) => ({ status, message }))).toEqual(witness.errors)
  })

  it("should carry the witness's request: method, path, and each argument sent under its own name in the body", () => {
    expect(fn.name).toBe("notion.search_workspace")
    expect(fn.class).toBe("read")
    expect({ method: fn.request.method, path: fn.request.path, pathParams: fn.request.pathParams, query: fn.request.query }).toEqual({
      method: witness.method,
      path: witness.path,
      pathParams: [],
      query: {},
    })
    expect(fn.request.body).toEqual(Object.fromEntries(witness.bodyKeys.map((key) => [key, key])))
  })

  it("should build the request the witness sends for the same arguments", () => {
    const args = { query: "roadmap", filter: { value: "page", property: "object" } }
    expect(ajv.validate(fn.schema, args)).toBe(true)
    expect(requestOf(connector, fn, args)).toEqual({
      url: "https://api.notion.com/v1/search",
      method: "POST",
      headers: { "Notion-Version": "2025-09-03" },
      body: { query: "roadmap", filter: { value: "page", property: "object" } },
    })
  })

  it("should accept the witness's examples and refuse a key the description does not declare", () => {
    const validate = ajv.compile(fn.schema)
    for (const example of witness.examples) expect(validate(example)).toBe(true)
    expect(validate({ query: "x", page_size: 10 })).toBe(false)
  })

  it("should declare the cursor pagination that the witness leaves to the caller, stopped by has_more", () => {
    expect(fn.pagination).toEqual({ kind: "cursor", requestParam: "start_cursor", next: "next_cursor", more: "has_more", maxPages: 10 })
    expect(ajv.validate(fn.schema, { all_pages: true, max_pages: 11 })).toBe(false)
  })
})
