// Chaque définition générée porte un nom qualifié et un JSON Schema d'entrée que valide un validateur JSON Schema 2020-12
// standard (Ajv) : ses exemples passent, une clé inconnue est refusée. `format` reste une annotation, comme dans le test
// des descriptions côté Python ; le mode strict d'Ajv signale tout mot-clé inconnu ou mal placé.
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { connectors } from "../src"

const ajv = new Ajv2020({ strictTypes: false, strictTuples: false, validateFormats: false, allErrors: true })
const functions = connectors.flatMap((connector) => connector.functions.map((fn) => [fn.name, connector, fn] as const))

describe("generated definitions", () => {
  it("should generate at least one function per connector", () => {
    for (const connector of connectors) expect(connector.functions.length, connector.name).toBeGreaterThan(0)
  })

  it.each(functions)("%s: qualified name, strict root, examples accepted, unknown key refused", (name, connector, fn) => {
    expect(name).toBe(`${connector.name}.${name.slice(connector.name.length + 1)}`)
    expect(fn.connector).toBe(connector.name)
    expect(fn.schema.type).toBe("object")
    expect(fn.schema.additionalProperties).toBe(false)
    const validate = ajv.compile(fn.schema)
    expect(fn.examples.length).toBeGreaterThan(0)
    for (const example of fn.examples) {
      expect(validate(example.input), `${example.title}: ${ajv.errorsText(validate.errors)}`).toBe(true)
    }
    const first = fn.examples[0]
    expect(validate({ ...first?.input, __unknown__: 1 })).toBe(false)
  })

  it.each(functions)("%s: every path parameter and mapped argument names a schema key", (_name, _connector, fn) => {
    const keys = Object.keys(fn.schema.properties as object)
    for (const param of fn.request.pathParams) expect(keys).toContain(param)
    for (const argument of [...Object.values(fn.request.query), ...Object.values(fn.request.body), ...Object.values(fn.request.headers)]) {
      expect(keys).toContain(argument)
    }
    for (const argument of Object.keys(fn.request.encode ?? {})) expect(keys).toContain(argument)
  })

  it.each(connectors.filter((c) => c.probe).map((c) => [c.name, c] as const))("%s: the probe is a read function callable with {}", (_name, connector) => {
    const probe = connector.functions.find((fn) => fn.name === connector.probe?.function)
    expect(probe?.class).toBe("read")
    expect(ajv.validate(probe?.schema as object, {})).toBe(true)
  })

  const consents = connectors.flatMap((c) => (c.auth.kind === "oauth2_user" ? [[c.name, c, c.auth] as const] : []))
  it.each(consents)("%s: the identity of a consent is a read function callable with {}, and no credential", (_name, connector, auth) => {
    const identity = connector.functions.find((fn) => fn.name === auth.identity.function)
    expect(identity?.class).toBe("read")
    expect(ajv.validate(identity?.schema as object, {})).toBe(true)
    expect(connector.credential).toEqual([])
  })

  it.each(connectors.filter((c) => c.baseUrls).map((c) => [c.name, c] as const))("%s: base URLs give one address per choice", (_name, connector) => {
    const setting = connector.settings?.find((s) => s.name === connector.baseUrls?.setting)
    expect(setting?.type).toBe("choice")
    if (setting?.type !== "choice") return
    expect(Object.keys(connector.baseUrls?.values ?? {}).sort()).toEqual([...setting.choices].sort())
    if (setting.default !== undefined) expect(setting.choices).toContain(setting.default)
    expect(connector.baseUrl).toBeUndefined()
  })
})
