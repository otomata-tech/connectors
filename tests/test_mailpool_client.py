"""Mailpool client contract (v1, `X-Api-Authorization`, cold-email infrastructure).

Mocks `requests.Session.request`: verbs and paths, auth header, bounds, retries
on reads only — and Mailpool's own traps, each locked by a test that would fail
if the fix were removed:

- no mailbox credential ever leaves the client, at any depth (spam checks embed
  a full mailbox);
- every list sends `limit` and `offset`, even when the caller omits them;
- `update_domain_dns` replaces the whole set: an empty list is refused;
- ids placed in the path are positive integers only.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.mailpool import client as mp


class _Resp:
    def __init__(self, status_code: int = 200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = b"x"
        self.text = str(self._body)
        self.headers = {}

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

    monkeypatch.setattr(mp.requests.Session, "request", fake_request)
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)
    return seen


@pytest.fixture()
def cli():
    return mp.MailpoolClient(api_key="mp_test")


BASE = "https://app.mailpool.io/v1/api"

MAILBOX = {
    "id": 5301, "email": "anna@example-outreach.com", "type": "shared",
    "status": "active", "password": "p1", "imapHost": "imap.example.net",
    "imapPassword": "p2", "smtpPassword": "p3", "secret": "s1",
    "admin": {"email": "admin@example-outreach.com", "password": "p4", "secret": "s2"},
    "domain": {"id": 1201, "domain": "example-outreach.com"},
}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_requires_key():
    from oto.tools.common.credentials import MissingCredential
    with pytest.raises(MissingCredential):
        mp.MailpoolClient(api_key=None)


def test_auth_header(capture, cli):
    cli.get_subscription_slots()
    assert capture["headers"]["X-Api-Authorization"] == "mp_test"
    assert capture["url"] == f"{BASE}/subscriptions/slots"


@pytest.mark.parametrize("call", [
    lambda c: c.list_mailboxes(),
    lambda c: c.get_mailbox(5301),
    lambda c: c.get_spam_check(880011),
    lambda c: c.create_spam_check(5301),
])
def test_no_credential_is_ever_returned(capture, cli, call):
    capture["responses"].append(_Resp(200, {
        "data": [MAILBOX], "mailbox": MAILBOX, **MAILBOX}))
    out = call(cli)
    leaked = [k for k in _keys(out) if mp.SECRET_KEYS.search(k)]
    assert leaked == []
    assert out["mailbox"]["admin"] == {"email": "admin@example-outreach.com"}
    assert out["data"][0]["imapHost"] == "imap.example.net"


def test_strip_secrets_keeps_everything_else():
    assert mp.strip_secrets({"a": [{"b": 1, "smtpPassword": "x"}], "c": None}) == {
        "a": [{"b": 1}], "c": None}


@pytest.mark.parametrize("call,path", [
    (lambda c: c.list_domains(), "/domains/"),
    (lambda c: c.list_mailboxes(), "/mailboxes"),
    (lambda c: c.list_spam_checks(), "/spam-checks/"),
    (lambda c: c.list_warmup_inboxes(), "/warmup"),
    (lambda c: c.list_available_warmup_inboxes(), "/warmup/available-inboxes"),
])
def test_lists_always_send_limit_and_offset(capture, cli, call, path):
    call(cli)
    assert capture["url"] == BASE + path
    assert capture["kwargs"]["params"] == {"limit": mp.DEFAULT_LIMIT, "offset": 0}


def test_mailboxes_domain_filter(capture, cli):
    cli.list_mailboxes(limit=10, offset=20, domain_id=1201)
    assert capture["kwargs"]["params"] == {"limit": 10, "offset": 20, "domainId": 1201}


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (10, -1), ("10", 0), (True, 0)])
def test_page_bounds(cli, limit, offset):
    with pytest.raises(ValueError):
        cli.list_domains(limit=limit, offset=offset)


@pytest.mark.parametrize("bad", [0, -3, "12", "../x", True, None])
def test_path_ids_are_positive_integers(cli, bad):
    with pytest.raises(ValueError):
        cli.get_domain(bad)


def test_update_dns_sends_the_whole_set(capture, cli):
    records = [{"type": "MX", "key": None, "value": "mx.example.net", "priority": 10},
               {"type": "CNAME", "key": "track", "value": "t.example.net"}]
    cli.update_domain_dns(1201, records)
    assert (capture["method"], capture["url"]) == ("PUT", f"{BASE}/domains/1201/dns")
    assert capture["kwargs"]["json"] == records


@pytest.mark.parametrize("records", [
    [],
    [{"type": "XX", "value": "v"}],
    [{"type": "A", "value": ""}],
    [{"type": "A", "value": "1.2.3.4", "ttl": 60}],
])
def test_update_dns_refuses_bad_sets(capture, cli, records):
    with pytest.raises(ValueError):
        cli.update_domain_dns(1201, records)
    assert capture["calls"] == []


def test_dmarc_and_redirect_bodies(capture, cli):
    cli.set_dmarc_email(1201, "dmarc@example.com")
    assert capture["kwargs"]["json"] == {"email": "dmarc@example.com"}
    cli.set_dmarc_policy(1201, "reject")
    assert capture["url"] == f"{BASE}/domains/1201/dmarc-policy"
    cli.set_redirect_url(1201, " example.com ")
    assert capture["kwargs"]["json"] == {"url": "example.com"}
    with pytest.raises(ValueError):
        cli.set_dmarc_policy(1201, "strict")
    with pytest.raises(ValueError):
        cli.set_dmarc_email(1201, "not-an-email")


def test_domain_lookups(capture, cli):
    cli.get_domain_info(" Example-Outreach.com ")
    assert capture["kwargs"]["params"] == {"domain": "example-outreach.com"}
    cli.get_domain_suggestions("example", limit=5)
    assert capture["kwargs"]["params"] == {"domain": "example", "limit": 5, "offset": 0}
    cli.check_microsoft_365_availability("example-outreach.com")
    assert capture["method"] == "POST"
    with pytest.raises(ValueError):
        cli.get_domain_info("nodot")


def test_reads_are_retried_writes_are_not(capture, cli):
    capture["responses"] += [_Resp(503), _Resp(200, {"ok": 1})]
    assert cli.get_domain(1201) == {"ok": 1}
    assert len(capture["calls"]) == 2

    capture["calls"].clear()
    capture["responses"] += [_Resp(503, {"message": "down"})]
    with pytest.raises(UpstreamHTTPError) as e:
        cli.create_spam_check(5301)
    assert e.value.status_code == 503
    assert len(capture["calls"]) == 1


def test_upstream_error_is_typed(capture, cli):
    capture["responses"].append(_Resp(403, {"message": "not enabled for this workspace"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.check_google_workspace_availability("example-outreach.com")
    assert e.value.status_code == 403
