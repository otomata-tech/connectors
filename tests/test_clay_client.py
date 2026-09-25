"""Client Clay : transport de l'API publique, webhooks de table, lecture du cURL."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from oto.tools.clay import ClayClient, ClayTableWebhook, is_clay_webhook_url, parse_curl
from oto.tools.common import UpstreamHTTPError

HOOK = "https://api.clay.com/v3/sources/webhook/pull-in-data-from-a-webhook-abc"


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.content = b"{}" if body is not None else b""
    r.text = str(body)
    r.headers = headers or {}
    return r


def test_cle_en_entete_et_base_url():
    c = ClayClient(api_key="k")
    assert c.session.headers["clay-api-key"] == "k"
    c.session.request = MagicMock(return_value=_resp(body={"user": {}}))
    c.get_me()
    method, url = c.session.request.call_args.args
    assert (method, url) == ("GET", "https://api.clay.com/public/v0/me")


def test_run_routine_envoie_items_et_webhook():
    c = ClayClient(api_key="k")
    c.session.request = MagicMock(return_value=_resp(body={"routine_run_id": "r"}))
    c.run_routine("function:t_1", [{"id": "a", "inputs": {}}], webhook_id="wh")
    args, kw = c.session.request.call_args
    assert args[1].endswith("/routines/function:t_1/run")
    assert kw["json"] == {"items": [{"id": "a", "inputs": {}}], "webhook_id": "wh"}


def test_429_porte_retry_after():
    c = ClayClient(api_key="k")
    c.session.request = MagicMock(return_value=_resp(
        429, {"message": "slow down"}, {"Retry-After": "7"}))
    with pytest.raises(UpstreamHTTPError) as e:
        c.get_credit_balance()
    assert e.value.status_code == 429
    assert e.value.body["retry_after"] == "7"


def test_401_type():
    c = ClayClient(api_key="k")
    c.session.request = MagicMock(return_value=_resp(401, {"message": "Authentication failed"}))
    with pytest.raises(UpstreamHTTPError) as e:
        c.get_me()
    assert e.value.status_code == 401


@pytest.mark.parametrize("url,ok", [
    (HOOK, True),
    ("https://clay.com/x", True),
    ("http://api.clay.com/x", False),
    ("https://api.clay.com.evil.io/x", False),
    ("https://evilclay.com/x", False),
    ("", False),
])
def test_garde_hote(url, ok):
    assert is_clay_webhook_url(url) is ok


def test_parse_curl_multiligne_avec_jeton():
    cmd = (f"curl -X POST '{HOOK}' \\\n"
           "  -H 'Content-Type: application/json' \\\n"
           "  -H 'x-clay-webhook-auth: tok123' \\\n"
           "  -d '{\"name\": \"x\"}'")
    assert parse_curl(cmd) == {"webhook_url": HOOK, "auth_token": "tok123"}


def test_parse_curl_url_nue():
    assert parse_curl(f"  {HOOK}  ") == {"webhook_url": HOOK, "auth_token": None}


def test_parse_curl_sans_url():
    with pytest.raises(ValueError):
        parse_curl("curl -X POST -H 'a: b'")


def test_webhook_refuse_un_hote_tiers():
    with pytest.raises(ValueError):
        ClayTableWebhook("https://example.com/hook")


def test_webhook_push_une_ligne_avec_jeton():
    w = ClayTableWebhook(HOOK, auth_token="tok")
    assert w.session.headers["x-clay-webhook-auth"] == "tok"
    w.session.post = MagicMock(return_value=_resp(body={"ok": True}))
    assert w.push({"name": "Acme"}) == {"ok": True}
    assert w.session.post.call_args.kwargs["json"] == {"name": "Acme"}


@pytest.mark.parametrize("glue", [" \\  ", " \\", "\\ "])
def test_parse_curl_colle_dans_un_champ_une_ligne(glue):
    """Un <input> une ligne retire les sauts : les `\\` de continuation restent."""
    cmd = (f"curl -X POST '{HOOK}'{glue}-H 'Content-Type: application/json'{glue}"
           f"-H 'x-clay-webhook-auth: tok'{glue}-d '{{\"a\": 1}}'")
    assert parse_curl(cmd) == {"webhook_url": HOOK, "auth_token": "tok"}
