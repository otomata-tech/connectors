"""La fabrique : règles hors schéma, JSON Schema d'entrée recopié, ajouts du format, refus, sortie à jour.

La sortie TypeScript commitée (`ts/src/`) doit être celle que la fabrique produit des
descriptions : sinon `test_committed_output_is_current` échoue et dit de lancer
`python -m fabrique`.
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest
import yaml

from fabrique.build import generate, stale
from fabrique.descriptions import CONNECTORS, DescriptionError, check
from fabrique.typescript import NotGenerated, connector_module, function_source, js_block

SELLSY = CONNECTORS / "sellsy" / "connector.yaml"
NOTION = CONNECTORS / "notion" / "connector.yaml"
PENNYLANE = CONNECTORS / "pennylane" / "connector.yaml"


def _load(path: pathlib.Path) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- Sortie commitée -------------------------------------------------------------


def test_committed_output_is_current():
    assert stale(generate()) == [], "ts/src is out of date: run `python -m fabrique`"


def test_every_description_generates_a_module():
    result = generate()
    assert {m.connector for m in result.modules} == {p.parent.name for p in CONNECTORS.glob("*/connector.yaml")}
    assert all(m.generated for m in result.modules)


def test_every_function_is_generated():
    result = generate()
    assert [s.function for m in result.modules for s in m.skipped] == []


# --- Règle 1 : un argument, un seul endroit ---------------------------------------


def _get_estimate(d):
    return next(f for f in d["functions"] if f["name"] == "get_estimate")


@pytest.mark.parametrize("mutate, message", [
    (lambda d: _get_estimate(d)["call"].update(body={"id": "id"}), "goes to two places"),
    (lambda d: _get_estimate(d)["call"].update(path="/estimates/{estimate_id}"), "the path names 'estimate_id'"),
    (lambda d: _get_estimate(d)["call"]["query"].update(other="nope"), "names 'nope', which is not an input argument"),
    (lambda d: _get_estimate(d)["call"].pop("query"), "go nowhere in call"),
    (lambda d: _get_estimate(d)["input"].update(required=[]), "path argument 'id' must be required"),
    (lambda d: d["functions"][0]["pagination"].update(request_param="cursor"), "request_param 'cursor' is not an input argument"),
], ids=["two_places", "path_unknown", "query_unknown", "unplaced", "optional_path", "pagination_unknown"])
def test_argument_rule_refuses(mutate, message):
    description = _load(SELLSY)
    mutate(description)
    with pytest.raises(DescriptionError, match=message):
        check(description, SELLSY)


# --- Règle 2 : une référence de auth nomme un champ de credential -----------------


@pytest.mark.parametrize("path, reference", [(SELLSY, "client_secret"), (NOTION, "token")])
def test_auth_reference_must_name_a_credential_field(path, reference):
    description = _load(path)
    description["connector"]["auth"][reference] = "missing_field"
    with pytest.raises(DescriptionError, match=rf"auth\.{reference} names 'missing_field', which is not a credential field"):
        check(description, path)


def test_schema_violation_is_refused_before_the_rules():
    description = _load(SELLSY)
    del description["functions"][0]["class"]
    with pytest.raises(DescriptionError, match="does not match the description schema"):
        check(description, SELLSY)


def test_valid_descriptions_pass():
    for path in sorted(CONNECTORS.glob("*/connector.yaml")):
        check(_load(path), path)


# --- Génération : refus nommés, pagination -----------------------------------------


def test_input_schema_is_copied_as_is():
    """Aucune traduction : le schéma servi est celui de la description, ancres YAML résolues, quels que soient ses mots-clés."""
    function = next(f for f in _load(NOTION)["functions"] if f["name"] == "edit_page_markdown")
    source = function_source("notion", function)
    assert "  schema: " + js_block(function["input"], 1) + ",\n" in source
    assert '"oneOf": [' in source and '"not": {' in source


@pytest.mark.parametrize("mutate", [
    lambda i: i.update(additionalProperties=True),
    lambda i: i.pop("additionalProperties"),
    lambda i: i.update(type="array"),
], ids=["open", "no_additional_properties", "not_an_object"])
def test_root_not_strict_is_skipped_and_named(mutate):
    """La seule exigence que la fabrique vérifie encore elle-même, au cas où le schéma du format la laisserait passer."""
    description = check(_load(NOTION), NOTION)
    mutate(description.data["functions"][0]["input"])
    module = connector_module(description)
    reason = "input: the root must be a strict object (type: object, additionalProperties: false)"
    assert [(s.function, s.reason) for s in module.skipped] == [("notion.search_workspace", reason)]
    assert f"// - notion.search_workspace: {reason}" in module.source
    assert "export const searchWorkspace" not in module.source
    assert "export const getPage" in module.source


def test_pagination_adds_its_arguments_and_refuses_a_clash():
    function = _load(NOTION)["functions"][0]
    source = function_source("notion", function)
    assert '"all_pages": {"type": "boolean"' in source and '"maximum": 10' in source
    function["input"]["properties"]["all_pages"] = {"type": "boolean"}
    with pytest.raises(NotGenerated, match="pagination adds"):
        function_source("notion", function)


def test_handwritten_function_is_not_generated():
    function = _load(NOTION)["functions"][0]
    del function["call"]
    function["handwritten"] = {"python": {"module": "m", "function": "f"}, "typescript": {"file": "f.ts", "export": "f"}}
    with pytest.raises(NotGenerated, match="handwritten"):
        function_source("notion", function)


# --- Ajouts du format : sortie ----------------------------------------------------


def _pennylane(name: str) -> dict:
    return next(f for f in _load(PENNYLANE)["functions"] if f["name"] == name)


def test_connector_carries_headers_rate_and_probe():
    notion = connector_module(check(_load(NOTION), NOTION)).source
    assert 'headers: {"Notion-Version": "2025-09-03"},' in notion
    pennylane = connector_module(check(_load(PENNYLANE), PENNYLANE)).source
    assert "rateLimit: { requests: 4, intervalMs: 1000 }," in pennylane
    assert 'probe: { function: "pennylane.get_company", nonEmpty: ["scopes"] },' in pennylane


def test_request_carries_constants_encoding_and_stop_flag():
    source = function_source("pennylane", _pennylane("list_customers"))
    assert 'encode: {"filter": "json"},' in source
    assert 'more: "has_more"' in source
    source = function_source("pennylane", _pennylane("create_customer_invoice"))
    assert 'constants: {"body": {"draft": true}},' in source


def test_checks_and_expectations_are_carried():
    source = function_source("pennylane", _pennylane("create_ledger_entry"))
    assert 'checks: [\n    { kind: "equal_sums", refusal: "entry_unbalanced", items: "ledger_entry_lines", fields: ["debit", "credit"] },' in source
    source = function_source("pennylane", _pennylane("get_quote_pdf_link"))
    assert 'expect: [\n    { kind: "non_empty", refusal: "pdf_missing", path: "public_file_url" },' in source


def test_delete_carries_its_body():
    source = function_source("pennylane", _pennylane("unletter_ledger_entry_lines"))
    assert 'method: "DELETE"' in source
    assert 'body: {"ledger_entry_lines": "ledger_entry_lines", "unbalanced_lettering_strategy": "unbalanced_lettering_strategy"},' in source


# --- Règles 3 à 5 : constantes, contrôles, sonde ------------------------------------


def _fn(d, name):
    return next(f for f in d["functions"] if f["name"] == name)


@pytest.mark.parametrize("path, mutate, message", [
    (PENNYLANE, lambda d: _fn(d, "create_customer_invoice")["call"]["constants"]["body"].update(label=True),
     "body 'label' is both a constant and an argument"),
    (NOTION, lambda d: d["connector"]["headers"].update(Authorization="x"),
     "header 'Authorization' is set twice"),
    (NOTION, lambda d: _fn(d, "get_page")["call"].update(constants={"headers": {"notion-version": "x"}}),
     "header 'notion-version' is set twice"),
    (PENNYLANE, lambda d: _fn(d, "get_product")["call"].update(encode={"product_id": "json"}),
     "encode names 'product_id', which is not placed in query, body or headers"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(refusal="nope"),
     "equal_sums names refusal 'nope', which the function does not declare"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(fields=["debit", "amount"]),
     "equal_sums needs 'ledger_entry_lines' to be a list argument whose items declare debit, amount"),
    (PENNYLANE, lambda d: _fn(d, "create_ledger_entry")["checks"][0].update(items="label"),
     "equal_sums needs 'label' to be a list argument"),
    (PENNYLANE, lambda d: _fn(d, "get_quote_pdf_link")["expect"][0].update(refusal="nope"),
     "non_empty names refusal 'nope'"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="nope"),
     "probe names 'nope', which is not a function of the connector"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="get_product"),
     "probe 'get_product' must be a read function with a call and no required argument"),
    (PENNYLANE, lambda d: d["connector"]["probe"].update(function="create_customer"),
     "probe 'create_customer' must be a read function"),
], ids=["constant_and_argument", "header_and_auth", "header_case", "encode_unplaced", "check_refusal",
        "check_fields", "check_not_a_list", "expect_refusal", "probe_unknown", "probe_required", "probe_write"])
def test_added_rules_refuse(path, mutate, message):
    description = _load(path)
    mutate(description)
    with pytest.raises(DescriptionError, match=re.escape(message)):
        check(description, path)
