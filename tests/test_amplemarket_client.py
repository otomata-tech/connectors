"""Contrat du client Amplemarket (Bearer, REST JSON, pagination par curseur).

Mocke `requests.Session.request`. Chaque exemple de la description est rejoué
contre le client : verbe, chemin, query et corps doivent être ceux que dit
`call` — la description et le client ne peuvent pas diverger. Puis les gardes
propres au client : re-tentative des seuls GET, refus avant appel, curseur.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from oto.tools.amplemarket import client as am
from oto.tools.amplemarket import next_page_after
from oto.tools.common.errors import UpstreamHTTPError

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = yaml.safe_load(
    (ROOT / "connectors" / "amplemarket" / "connector.yaml").read_text(encoding="utf-8"))
FUNCTIONS = DESCRIPTION["functions"]
BASE = "https://api.amplemarket.com"


class _Resp:
    def __init__(self, status_code: int = 200, body=None, content=b"x"):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content
        self.text = str(self._body)
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs, headers=dict(self.headers))
        seen["calls"].append((method, url))
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200)

    monkeypatch.setattr(am.requests.Session, "request", fake_request)
    monkeypatch.setattr(am.time, "sleep", lambda _s: None)
    return seen


@pytest.fixture()
def cli():
    return am.AmplemarketClient(api_key="am_test")


# --- la description rejouée contre le client ----------------------------------

def _q(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return [_q(v) for v in value]
    return value


def _cases():
    for function in FUNCTIONS:
        for example in function["examples"]:
            yield pytest.param(function, example["input"],
                               id=f"{function['name']}: {example['title']}")


@pytest.mark.parametrize("function,example", list(_cases()))
def test_each_example_sends_the_described_request(capture, cli, function, example):
    props = function["input"]["properties"]
    args = {k: v["default"] for k, v in props.items() if "default" in v}
    args.update(example)
    getattr(cli, function["name"])(**example)

    call = function["call"]
    path = call["path"]
    for name, value in args.items():
        path = path.replace("{" + name + "}", str(value))
    assert capture["method"] == call["method"]
    assert capture["url"] == BASE + path

    query = {api: _q(args[arg]) for api, arg in (call.get("query") or {}).items() if arg in args}
    assert (capture["kwargs"]["params"] or {}) == query

    body = {api: args[arg] for api, arg in (call.get("body") or {}).items() if arg in args}
    body.update((call.get("constants") or {}).get("body") or {})
    sent = capture["kwargs"]["json"]
    assert (sent if sent is not None else {}) == body


def test_every_function_is_a_client_method():
    for function in FUNCTIONS:
        assert callable(getattr(am.AmplemarketClient, function["name"], None)), function["name"]


def test_search_filters_are_the_described_ones():
    from oto.tools.amplemarket._api import prospecting as p
    by_name = {f["name"]: set(f["input"]["properties"]) for f in FUNCTIONS}
    assert by_name["search_people"] == set(p.PEOPLE_SEARCH_FILTERS) | {"page", "page_size"}
    assert by_name["search_companies"] == set(p.COMPANY_SEARCH_FILTERS) | {"page", "page_size"}


# --- auth, clé -----------------------------------------------------------------

def test_auth_is_bearer(capture, cli):
    cli.probe()
    assert capture["headers"]["Authorization"] == "Bearer am_test"
    assert capture["calls"] == [("GET", f"{BASE}/account-info")]
    assert capture["kwargs"]["allow_redirects"] is False


def test_missing_key_is_named_never_read_from_env(monkeypatch):
    from oto.tools.common.credentials import MissingCredential
    monkeypatch.setenv("AMPLEMARKET_API_KEY", "am_env")
    with pytest.raises(MissingCredential, match="AMPLEMARKET_API_KEY"):
        am.AmplemarketClient()


# --- transport -------------------------------------------------------------------

def test_get_is_retried_on_429_then_succeeds(capture, cli):
    capture["responses"] = [_Resp(429), _Resp(200, {"users": []})]
    assert cli.list_users() == {"users": []}
    assert len(capture["calls"]) == 2


def test_a_write_is_never_replayed(capture, cli):
    capture["responses"] = [_Resp(503, {"_errors": []})]
    with pytest.raises(UpstreamHTTPError) as e:
        cli.add_leads_to_sequence("seq_1", [{"email": "a@example.com"}])
    assert e.value.status_code == 503
    assert len(capture["calls"]) == 1


def test_upstream_error_keeps_status_and_body(capture, cli):
    body = {"_errors": [{"code": "insufficient_credits"}]}
    capture["responses"] = [_Resp(402, body)]
    with pytest.raises(UpstreamHTTPError) as e:
        cli.find_person(email="a@example.com", reveal_email=True)
    assert e.value.status_code == 402 and e.value.body == body


def test_redirect_is_refused(capture, cli):
    from oto.tools.http.client import RedirectRefused
    capture["responses"] = [_Resp(302)]
    with pytest.raises(RedirectRefused):
        cli.get_account_info()


def test_empty_body_answers_empty_dict(capture, cli):
    capture["responses"] = [_Resp(200, content=b"")]
    assert cli.skip_task("tsk_1") == {}


def test_unset_arguments_are_dropped_and_lists_repeat(capture, cli):
    cli.list_accounts(tags=["vip", "fr"])
    assert capture["kwargs"]["params"] == {"tags[]": ["vip", "fr"]}


def test_path_ids_are_encoded(capture, cli):
    cli.get_contact_by_email("jane+test@example.com")
    assert capture["url"] == f"{BASE}/contacts/email/jane%2Btest@example.com"
    cli.get_contact("a/b")
    assert capture["url"] == f"{BASE}/contacts/a%2Fb"


# --- refus avant appel ------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda c: c.find_person(name="Jane Doe"),
    lambda c: c.find_company(),
    lambda c: c.list_job_openings(),
    lambda c: c.list_contacts(),
    lambda c: c.list_contacts(ids=[str(i) for i in range(21)]),
    lambda c: c.start_person_enrichment([]),
    lambda c: c.add_leads_to_sequence("seq_1", []),
    lambda c: c.create_lead_list("jane@example.com", "phone", [{"email": "a@b.c"}]),
    lambda c: c.update_mailbox_daily_limit("mbx_1", -1),
    lambda c: c.search_people(person_title=["CEO"]),
    lambda c: c.get_contact(" "),
])
def test_bad_input_is_refused_before_any_call(capture, cli, call):
    with pytest.raises(ValueError):
        call(cli)
    assert capture["calls"] == []


def test_emails_may_be_given_as_strings(capture, cli):
    cli.add_excluded_emails(["a@example.com"])
    assert capture["kwargs"]["json"] == {"excluded_emails": [{"email": "a@example.com"}]}


# --- pagination ---------------------------------------------------------------------

def test_next_page_after_reads_the_next_link():
    page = {"_links": {"next": {"href": "/users?page[size]=10&page[after]=usr_9"}}}
    assert next_page_after(page) == "usr_9"
    assert next_page_after({"_links": {"self": {"href": "/users"}}}) is None
    assert next_page_after(None) is None


# --- chaque argument déclaré, au moins une fois ------------------------------------
# Les exemples sont minimaux : un nom d'API mal recopié sur un argument qu'aucun
# exemple ne pose passerait. Ici, CHAQUE propriété de chaque fonction est posée.

def _sample(schema: dict):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "boolean":
        return True
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "array":
        return [_sample(schema.get("items") or {"type": "string"})]
    if kind == "object":
        return {k: _sample(v) for k, v in (schema.get("properties") or {}).items()}
    return "x1"


def _full_input(function):
    props = function["input"]["properties"]
    args = {k: _sample(v) for k, v in props.items()}
    if function["name"] == "list_contacts":
        args["ids"] = ["c1"]
    return args


@pytest.mark.parametrize("function", FUNCTIONS, ids=lambda f: f["name"])
def test_every_argument_reaches_its_described_place(capture, cli, function):
    args = _full_input(function)
    getattr(cli, function["name"])(**args)
    call = function["call"]
    path = call["path"]
    for name, value in args.items():
        path = path.replace("{" + name + "}", str(value))
    assert (capture["method"], capture["url"]) == (call["method"], BASE + path)
    query = {api: _q(args[arg]) for api, arg in (call.get("query") or {}).items()}
    assert (capture["kwargs"]["params"] or {}) == query
    body = {api: args[arg] for api, arg in (call.get("body") or {}).items()}
    body.update((call.get("constants") or {}).get("body") or {})
    sent = capture["kwargs"]["json"]
    assert (sent if sent is not None else {}) == body
