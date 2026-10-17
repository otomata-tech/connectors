"""Contrat du client Malibou (Public API v1, Bearer, paie et RH en lecture seule).

Mocke `requests.Session.request` : en-tête d'auth, chemins sous
`/organizations/{organization_id}`, query camelCase de l'API, bornes (dates
`YYYY-MM-DD`, fenêtre ordonnée, page et limit positifs), énumérations, ids de
chemin, re-tentatives sur 429, redirection refusée — et la clé jamais lue de
l'environnement. Chaque exemple de la description est rejoué contre le client.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from oto.tools.malibou import client as ml
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.http.client import RedirectRefused

DESCRIPTION = (pathlib.Path(__file__).resolve().parent.parent
               / "connectors" / "malibou" / "connector.yaml")


class _Resp:
    def __init__(self, status_code: int = 200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {"data": []}
        self.content = b"x"
        self.text = str(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs,
                    headers=dict(self.headers))
        seen["calls"].append((method, url))
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200)

    monkeypatch.setattr(ml.requests.Session, "request", fake_request)
    monkeypatch.setattr(ml.time, "sleep", lambda _s: None)
    return seen


@pytest.fixture()
def cli():
    return ml.MalibouClient(api_key="mk_test", organization_id="org_test")


BASE = "https://app.malibou.com/api/public/v1/organizations/org_test"


# --- auth, sonde ------------------------------------------------------------

def test_auth_header_is_bearer_and_probe_reads_one_collaborator(capture, cli):
    cli.probe()
    assert capture["headers"]["Authorization"] == "Bearer mk_test"
    assert capture["calls"] == [("GET", f"{BASE}/collaborators")]
    assert capture["kwargs"]["params"] == {"limit": 1}
    assert capture["kwargs"]["allow_redirects"] is False
    assert capture["kwargs"]["timeout"] == ml.HTTP_TIMEOUT


@pytest.mark.parametrize("kwargs, name", [
    ({"organization_id": "org_test"}, "MALIBOU_API_KEY"),
    ({"api_key": "mk_test"}, "MALIBOU_ORGANIZATION_ID"),
])
def test_missing_credential_is_named_never_read_from_env(monkeypatch, kwargs, name):
    """The library never reads secrets: an absent field is a named refusal."""
    from oto.tools.common.credentials import MissingCredential
    monkeypatch.setenv("MALIBOU_API_KEY", "mk_env")
    monkeypatch.setenv("MALIBOU_ORGANIZATION_ID", "org_env")
    with pytest.raises(MissingCredential, match=name):
        ml.MalibouClient(**kwargs)


@pytest.mark.parametrize("org", ["org/../x", "org?x=1", "a b", "x" * 65])
def test_organization_id_cannot_reshape_the_url(org):
    with pytest.raises(ValueError, match="organization_id"):
        ml.MalibouClient(api_key="mk_test", organization_id=org)


# --- query & chemins ----------------------------------------------------------

def test_list_collaborators_maps_query_to_camel_case(capture, cli):
    cli.list_collaborators(establishment_id="est_1", contract_status="active",
                           page=2, limit=50)
    assert capture["calls"] == [("GET", f"{BASE}/collaborators")]
    assert capture["kwargs"]["params"] == {
        "establishmentId": "est_1", "contractStatus": "active",
        "page": 2, "limit": 50}


def test_list_collaborators_sends_no_query_by_default(capture, cli):
    cli.list_collaborators()
    assert capture["kwargs"]["params"] is None


def test_get_collaborator_path(capture, cli):
    cli.get_collaborator("col_42", establishment_id="est_1")
    assert capture["calls"] == [("GET", f"{BASE}/collaborators/col_42")]
    assert capture["kwargs"]["params"] == {"establishmentId": "est_1"}


@pytest.mark.parametrize("method, path", [
    ("list_absences", "/absences"),
    ("list_work_locations", "/work-locations"),
])
def test_windowed_lists_map_query(capture, cli, method, path):
    getattr(cli, method)("2026-10-01", "2026-10-31", status="approved",
                         establishment_id="est_1", page=1, limit=100)
    assert capture["calls"] == [("GET", f"{BASE}{path}")]
    assert capture["kwargs"]["params"] == {
        "startDate": "2026-10-01", "endDate": "2026-10-31",
        "status": "approved", "establishmentId": "est_1",
        "page": 1, "limit": 100}


# --- validation -------------------------------------------------------------

def test_bad_date_is_refused(cli):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        cli.list_absences("01/10/2026", "2026-10-31")


def test_end_before_start_is_refused(cli):
    with pytest.raises(ValueError, match="before"):
        cli.list_work_locations("2026-10-31", "2026-10-01")


@pytest.mark.parametrize("bad", [0, -1, True, "2"])
def test_page_and_limit_must_be_positive_integers(cli, bad):
    with pytest.raises(ValueError, match="page"):
        cli.list_collaborators(page=bad)
    with pytest.raises(ValueError, match="limit"):
        cli.list_absences("2026-10-01", "2026-10-31", limit=bad)


def test_unknown_enums_are_refused(cli):
    with pytest.raises(ValueError, match="contract_status"):
        cli.list_collaborators(contract_status="current")
    with pytest.raises(ValueError, match="status"):
        cli.list_absences("2026-10-01", "2026-10-31", status="accepted")


def test_collaborator_id_cannot_reshape_the_url(cli):
    with pytest.raises(ValueError, match="collaborator_id"):
        cli.get_collaborator("../absences")


# --- transport --------------------------------------------------------------

def test_upstream_error_is_raised_with_service(capture, cli):
    capture["responses"] = [_Resp(403, body={"error": "Forbidden"})]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.list_collaborators()
    assert exc.value.status_code == 403
    assert exc.value.service == "malibou"
    assert len(capture["calls"]) == 1


def test_429_is_retried_then_succeeds(capture, cli):
    capture["responses"] = [_Resp(429), _Resp(200, body={"data": [{"id": "c1"}]})]
    assert cli.list_collaborators() == {"data": [{"id": "c1"}]}
    assert len(capture["calls"]) == 2


def test_retries_are_bounded(capture, cli):
    capture["responses"] = [_Resp(503)] * ml.MAX_ATTEMPTS
    with pytest.raises(UpstreamHTTPError):
        cli.list_collaborators()
    assert len(capture["calls"]) == ml.MAX_ATTEMPTS


def test_redirect_is_refused(capture, cli):
    capture["responses"] = [_Resp(302, headers={"Location": "https://elsewhere.example"})]
    with pytest.raises(RedirectRefused):
        cli.list_collaborators()


# --- la description, rejouée ----------------------------------------------------

def _examples():
    doc = yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))
    for fn in doc["functions"]:
        for ex in fn["examples"]:
            yield pytest.param(fn, ex["input"], id=f'{fn["name"]}:{ex["title"]}')


@pytest.mark.parametrize("fn, args", list(_examples()))
def test_every_example_replays_as_described(capture, cli, fn, args):
    getattr(cli, fn["name"])(**args)
    call = fn["call"]
    path = call["path"].format(**args)
    assert capture["calls"] == [(call["method"], f"{BASE}{path}")]
    expected = {api: args[arg] for api, arg in (call.get("query") or {}).items()
                if arg in args}
    assert (capture["kwargs"]["params"] or {}) == expected
