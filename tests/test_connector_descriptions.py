"""Les fichiers de description de connecteur respectent leur schéma.

Chaque `connectors/<nom>/connector.yaml` est validé contre
`connectors/connector.schema.json` (JSON Schema 2020-12), et chaque exemple d'une
fonction contre le schéma d'entrée de cette fonction. Des variantes invalides du
fichier Sellsy prouvent que la validation refuse ce qu'elle doit refuser.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent / "connectors"
SCHEMA = json.loads((ROOT / "connector.schema.json").read_text(encoding="utf-8"))
DESCRIPTIONS = sorted(ROOT.glob("*/connector.yaml"))


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate(description: dict) -> None:
    Draft202012Validator(SCHEMA).validate(description)
    for function in description["functions"]:
        validator = Draft202012Validator(function["input"])
        for example in function["examples"]:
            validator.validate(example["input"])


def test_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_sellsy_description_is_present():
    assert ROOT / "sellsy" / "connector.yaml" in DESCRIPTIONS


@pytest.mark.parametrize("path", DESCRIPTIONS, ids=lambda p: p.parent.name)
def test_description_is_valid(path):
    description = _load(path)
    _validate(description)
    assert description["connector"]["name"] == path.parent.name


def _sellsy() -> dict:
    return copy.deepcopy(_load(ROOT / "sellsy" / "connector.yaml"))


def _drop_class(d):
    del d["functions"][0]["class"]


def _unknown_key(d):
    d["connector"]["unknown"] = True


def _call_and_handwritten(d):
    d["functions"][0]["handwritten"] = {
        "python": {"module": "oto.tools.sellsy.client", "function": "list_estimates"},
        "typescript": {"file": "sellsy.ts", "export": "listEstimates"},
    }


def _neither_call_nor_handwritten(d):
    del d["functions"][1]["call"]


def _sensitive_without_confirm(d):
    d["functions"][0]["class"] = "sensitive"


def _input_not_strict(d):
    d["functions"][0]["input"]["additionalProperties"] = True


def _no_examples(d):
    d["functions"][0]["examples"] = []


def _example_outside_input(d):
    d["functions"][0]["examples"][0]["input"]["limit"] = 1000


def _example_unknown_argument(d):
    d["functions"][1]["examples"][0]["input"]["nope"] = 1


def _embed_not_documented(d):
    d["functions"][1]["examples"][0]["input"]["embed"] = ["owner"]


def _auth_missing_token_url(d):
    del d["connector"]["auth"]["token_url"]


def _credential_missing(d):
    del d["connector"]["credential"]


def _quota_without_platform(d):
    d["connector"]["quota"] = {"per_month": 1000}


def _bad_version(d):
    d["connector"]["version"] = "1.0"


def _bad_exposure(d):
    d["exposure"]["mode"] = "per_tool"


def _output_schema_not_strict(d):
    d["functions"][0]["output"]["schema"]["additionalProperties"] = True


def _output_schema_no_properties(d):
    del d["functions"][0]["output"]["schema"]["properties"]


def _rate_limit_without_window(d):
    d["connector"]["rate_limit"] = {"requests": 4}


def _constant_header_not_a_string(d):
    d["connector"]["headers"] = {"X-Api-Version": 2}


def _unknown_encoding(d):
    d["functions"][0]["call"]["encode"] = {"limit": "csv"}


def _unknown_check_kind(d):
    d["functions"][0]["checks"] = [{"kind": "positive", "refusal": "invalid_request", "items": "limit", "fields": ["a", "b"]}]


def _expectation_without_path(d):
    d["functions"][1]["expect"] = [{"kind": "non_empty", "refusal": "estimate_not_found"}]


def _probe_extra_key(d):
    d["connector"]["probe"] = {"function": "get_estimate", "covers": "quota"}


def _empty_stop_flag(d):
    d["functions"][0]["pagination"]["more"] = ""


@pytest.mark.parametrize("mutate", [
    _drop_class, _unknown_key, _call_and_handwritten, _neither_call_nor_handwritten,
    _sensitive_without_confirm, _input_not_strict, _no_examples, _example_outside_input,
    _example_unknown_argument, _embed_not_documented, _auth_missing_token_url,
    _credential_missing, _quota_without_platform, _bad_version, _bad_exposure,
    _output_schema_not_strict, _output_schema_no_properties,
    _rate_limit_without_window, _constant_header_not_a_string, _unknown_encoding, _unknown_check_kind,
    _expectation_without_path, _probe_extra_key, _empty_stop_flag,
], ids=lambda f: f.__name__.lstrip("_"))
def test_invalid_description_is_rejected(mutate):
    description = _sellsy()
    mutate(description)
    with pytest.raises(ValidationError):
        _validate(description)
