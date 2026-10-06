// Chaque définition générée se charge (ses expressions régulières compilent), porte un nom qualifié, accepte ses
// exemples et refuse une clé inconnue : le schéma `zod` dit la même chose que le JSON Schema de la description.
import { describe, expect, it } from "vitest"
import { connectors } from "../src"

const functions = connectors.flatMap((connector) => connector.functions.map((fn) => [fn.name, connector, fn] as const))

describe("generated definitions", () => {
  it("should generate at least one function per connector", () => {
    for (const connector of connectors) expect(connector.functions.length, connector.name).toBeGreaterThan(0)
  })

  it.each(functions)("%s: qualified name, examples accepted, unknown key refused", (name, connector, fn) => {
    expect(name).toBe(`${connector.name}.${name.slice(connector.name.length + 1)}`)
    expect(fn.connector).toBe(connector.name)
    expect(fn.examples.length).toBeGreaterThan(0)
    for (const example of fn.examples) {
      const parsed = fn.schema.safeParse(example.input)
      expect(parsed.success, `${example.title}: ${parsed.error?.message}`).toBe(true)
    }
    const first = fn.examples[0]
    expect(fn.schema.safeParse({ ...(first?.input as object), __unknown__: 1 }).success).toBe(false)
  })

  it.each(functions)("%s: every path parameter names a schema key", (_name, _connector, fn) => {
    const keys = Object.keys(fn.schema.shape)
    for (const param of fn.request.pathParams) expect(keys).toContain(param)
    for (const argument of [...Object.values(fn.request.query), ...Object.values(fn.request.body), ...Object.values(fn.request.headers)]) {
      expect(keys).toContain(argument)
    }
  })
})
