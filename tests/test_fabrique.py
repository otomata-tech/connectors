"""La fabrique : règles hors schéma, traduction JSON Schema → `zod`, refus, sortie à jour.

La sortie TypeScript commitée (`ts/src/`) doit être celle que la fabrique produit des
descriptions : sinon `test_committed_output_is_current` échoue et dit de lancer
`python -m fabrique`.
"""
from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

from fabrique.build import generate, stale
from fabrique.descriptions import CONNECTORS, DescriptionError, check
from fabrique.typescript import connector_module, function_source
from fabrique.zod import Untranslatable, translate, translate_input

SELLSY = CONNECTORS / "sellsy" / "connector.yaml"
NOTION = CONNECTORS / "notion" / "connector.yaml"


def _load(path: pathlib.Path) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- Sortie commitée -------------------------------------------------------------


def test_committed_output_is_current():
    assert stale(generate()) == [], "ts/src is out of date: run `python -m fabrique`"


def test_every_description_generates_a_module():
    result = generate()
    assert {m.connector for m in result.modules} == {p.parent.name for p in CONNECTORS.glob("*/connector.yaml")}
    assert all(m.generated for m in result.modules)


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


# --- Traduction JSON Schema → zod --------------------------------------------------


@pytest.mark.parametrize("schema, expected", [
    ({"type": "string", "minLength": 1, "maxLength": 9, "pattern": "^a"}, 'z.string().min(1).max(9).regex(new RegExp("^a"))'),
    ({"type": "integer", "minimum": 1, "maximum": 100}, "z.int().gte(1).lte(100)"),
    ({"type": "number", "exclusiveMinimum": 0}, "z.number().gt(0)"),
    ({"type": ["string", "null"]}, "z.string().nullable()"),
    ({"type": ["integer", "string"]}, "z.union([z.int(), z.string()])"),
    ({"enum": ["a", "b"]}, 'z.enum(["a", "b"])'),
    ({"enum": [0, 1, 2], "type": "integer"}, "z.literal([0, 1, 2])"),
    ({"enum": ["a", None]}, 'z.literal("a").nullable()'),
    ({"enum": []}, "z.never()"),
    ({"const": "object"}, 'z.literal("object")'),
    ({"enum": ["", "a"], "type": "string", "minLength": 1}, 'z.literal("a")'),
    ({"type": "array", "items": {"type": "integer"}, "minItems": 1}, "z.array(z.int()).min(1)"),
    ({"type": "object"}, "z.looseObject({})"),
    ({"type": "object", "additionalProperties": {"type": "string"}}, "z.record(z.string(), z.string())"),
    ({"anyOf": [{"type": "string"}, {"type": "integer", "minimum": 0}]}, "z.union([z.string(), z.int().gte(0)])"),
    ({"oneOf": [{"type": "string"}, {"type": "null"}]}, "z.union([z.string(), z.null()])"),
    ({"type": "string", "description": "Text."}, 'z.string().describe("Text.")'),
    ({"type": "string", "format": "uuid", "default": "x"}, 'z.string().meta({"default": "x", "format": "uuid"})'),
    ({"description": "Anything."}, 'z.unknown().describe("Anything.")'),
])
def test_translate(schema, expected):
    assert translate(schema) == expected


def test_translate_strict_object_with_optional_keys():
    schema = {
        "type": "object", "additionalProperties": False, "required": ["id"],
        "properties": {"id": {"type": "integer"}, "q": {"type": "string"}},
    }
    assert translate_input(schema, "input") == 'z.strictObject({\n  "id": z.int(),\n  "q": z.string().optional(),\n})'


def test_translate_one_of_exclusive_objects():
    page = {"type": "object", "additionalProperties": False, "required": ["page_id"], "properties": {"page_id": {"type": "string"}}}
    base = {"type": "object", "additionalProperties": False, "required": ["database_id"], "properties": {"database_id": {"type": "string"}}}
    assert translate({"oneOf": [page, base]}).startswith("z.union([z.strictObject(")


@pytest.mark.parametrize("schema, reason", [
    ({"oneOf": [{"type": "string"}, {"type": "string", "format": "uuid"}]}, "oneOf with branches that may overlap"),
    ({"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "uniqueItems needs a refinement"),
    ({"type": "string", "not": {"enum": ["."]}}, "not needs a refinement"),
    ({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]}, "required key"),
    ({"type": "string", "oneOf": [{"minLength": 1}]}, "oneOf with sibling constraints"),
    ({"minLength": 1}, "constraints without a type"),
    ({"const": {"a": 1}}, "object or array value"),
])
def test_translate_refuses(schema, reason):
    with pytest.raises(Untranslatable, match=reason):
        translate(schema)


@pytest.mark.parametrize("keyword, value", [
    ("oneOf", [{"required": ["a"]}, {"required": ["b"]}]),
    ("not", {"required": ["a", "b"]}),
    ("minProperties", 1),
    ("dependentRequired", {"a": ["b"]}),
])
def test_root_constraints_are_refused(keyword, value):
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, keyword: value,
    }
    with pytest.raises(Untranslatable, match=f"{keyword} at the root needs a refinement"):
        translate_input(schema, "input")


# --- Génération : refus nommés, pagination -----------------------------------------


def test_untranslatable_function_is_skipped_and_named():
    description = _load(NOTION)
    description["functions"][0]["input"]["minProperties"] = 1
    module = connector_module(check(description, NOTION))
    assert "notion.search_workspace" not in module.generated
    assert [s.function for s in module.skipped][0] == "notion.search_workspace"
    assert "// - notion.search_workspace: input: minProperties at the root needs a refinement" in module.source
    assert "export const searchWorkspace" not in module.source
    assert "export const getPage" in module.source


def test_pagination_adds_its_arguments_and_refuses_a_clash():
    function = _load(NOTION)["functions"][0]
    source = function_source("notion", function)
    assert '"all_pages": z.boolean()' in source and '"max_pages": z.int().gte(1).lte(10)' in source
    function["input"]["properties"]["all_pages"] = {"type": "boolean"}
    with pytest.raises(Untranslatable, match="pagination adds"):
        function_source("notion", function)


def test_handwritten_function_is_not_generated():
    function = _load(NOTION)["functions"][0]
    del function["call"]
    function["handwritten"] = {"python": {"module": "m", "function": "f"}, "typescript": {"file": "f.ts", "export": "f"}}
    with pytest.raises(Untranslatable, match="handwritten"):
        function_source("notion", function)
