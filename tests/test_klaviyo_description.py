"""The Klaviyo client follows its description: one method per function, same
arguments, and no module of 500 lines or more."""

import inspect
import pathlib

import pytest
import yaml

from oto.tools.klaviyo import KlaviyoClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = yaml.safe_load((ROOT / "connectors" / "klaviyo" / "connector.yaml").read_text(encoding="utf-8"))
FUNCTIONS = DESCRIPTION["functions"]


@pytest.mark.parametrize("function", FUNCTIONS, ids=lambda f: f["name"])
def test_each_function_is_a_method_with_the_same_arguments(function):
    method = getattr(KlaviyoClient, function["name"], None)
    assert callable(method), f"KlaviyoClient has no {function['name']}"
    params = inspect.signature(method).parameters
    declared = set(function["input"]["properties"])
    assert declared <= set(params)
    for name in function["input"].get("required", []):
        assert params[name].default is inspect.Parameter.empty


def test_class_and_confirmation_of_what_touches_consent_or_starts_flows():
    by_name = {f["name"]: f for f in FUNCTIONS}
    for name in ("subscribe_profiles", "unsubscribe_profiles", "add_profiles_to_list", "create_event"):
        assert by_name[name]["class"] == "sensitive" and by_name[name]["confirm"]["summary"]
    assert {f["name"] for f in FUNCTIONS if f["class"] == "write"} == {
        "create_or_update_profile", "remove_profiles_from_list"}


def test_revision_is_the_described_one():
    connector = DESCRIPTION["connector"]
    assert connector["headers"]["revision"] == connector["api_version"] == KlaviyoClient.REVISION
    assert connector["base_url"] == KlaviyoClient.BASE_URL


def test_modules_stay_under_500_lines():
    package = ROOT / "oto" / "tools" / "klaviyo"
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path
