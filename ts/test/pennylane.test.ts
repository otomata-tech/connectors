// Les ajouts du format, lus dans la définition générée de `pennylane` : ce que l'hôte exécute (rythme, sonde, filtre
// sérialisé en JSON, pagination arrêtée par `has_more`, constante de corps, contrôle avant l'appel, refus sur la
// réponse, `DELETE` avec corps). La définition décrit ; l'exécution revient à l'hôte.
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, it } from "vitest"
import { pennylane } from "../src"

const ajv = new Ajv2020({ strictTypes: false, strictTuples: false, validateFormats: false })

describe("pennylane definitions", () => {
  it("should declare the third party's rate and a probe that needs non-empty scopes", () => {
    expect(pennylane.connector.rateLimit).toEqual({ requests: 4, intervalMs: 1000 })
    expect(pennylane.connector.probe).toEqual({ function: "pennylane.get_company", nonEmpty: ["scopes"] })
  })

  it("should take the filter as a list of clauses, serialised as JSON in the query, and stop paging on has_more", () => {
    const fn = pennylane.listCustomers
    expect(fn.request.query.filter).toBe("filter")
    expect(fn.request.encode).toEqual({ filter: "json" })
    expect(fn.pagination?.more).toBe("has_more")
    const validate = ajv.compile(fn.schema)
    expect(validate({ filter: [{ field: "external_reference", operator: "in", value: ["A", "B"] }] })).toBe(true)
    expect(validate({ filter: [{ field: "external_reference", operator: "in", value: "A" }] })).toBe(false)
    expect(validate({ filter: [{ field: "external_reference", operator: "eq", value: ["A"] }] })).toBe(false)
    expect(validate({ filter: '[{"field":"name","operator":"eq","value":"Acme"}]' })).toBe(false)
    expect(validate({ filter: [{ field: "unknown", operator: "eq", value: "x" }] })).toBe(false)
  })

  it("should send draft as a body constant, not as an argument", () => {
    for (const fn of [pennylane.createCustomerInvoice, pennylane.createCreditNote, pennylane.createInvoiceFromQuote]) {
      expect(fn.request.constants).toEqual({ body: { draft: true } })
      expect(Object.keys(fn.schema.properties as object)).not.toContain("draft")
      expect(Object.values(fn.request.body)).not.toContain("draft")
    }
  })

  it("should check a ledger entry's balance before the call and the quote PDF link after it", () => {
    expect(pennylane.createLedgerEntry.checks).toEqual([
      { kind: "equal_sums", refusal: "entry_unbalanced", items: "ledger_entry_lines", fields: ["debit", "credit"] },
    ])
    expect(pennylane.createLedgerEntry.refusals.map((r) => r.code)).toContain("entry_unbalanced")
    expect(pennylane.getQuotePdfLink.expect).toEqual([{ kind: "non_empty", refusal: "pdf_missing", path: "public_file_url" }])
  })

  it("should send the body of a DELETE", () => {
    const fn = pennylane.unletterLedgerEntryLines
    expect(fn.request.method).toBe("DELETE")
    expect(fn.request.body).toEqual({ ledger_entry_lines: "ledger_entry_lines", unbalanced_lettering_strategy: "unbalanced_lettering_strategy" })
  })
})
