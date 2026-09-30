"""Engagement d'un post (réactions, commentaires) : pagination par `offset` et délai
du 429 (oto#177).

Unipile v2 pagine ces routes par `offset` SEUL ; le client ne passait qu'un `cursor`
sans effet, d'où le plafond à la première page. Et le délai d'un 429 ne se lisait que
dans le corps, jamais dans l'en-tête `Retry-After`.
"""
from unittest.mock import MagicMock

import pytest

from oto.tools.unipile.client import UnipileClient, UnipileRateLimited


def _client():
    c = UnipileClient.__new__(UnipileClient)
    c.base_url = "https://api.test/api/v2"
    c._account_id = "acc"
    c.account_id = lambda: "acc"
    c.session = MagicMock()
    return c


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.reason = "x"
    r.headers = headers or {}
    r.text = "{}" if body is not None else ""
    r.json.return_value = body
    return r


@pytest.mark.parametrize("meth,what", [("list_comments", "comments"),
                                       ("list_reactions", "reactions")])
def test_une_page_se_demande_par_offset(meth, what):
    c = _client()
    c.session.request.return_value = _resp(body={"data": [{"id": "a"}]})
    out = getattr(c, meth)("urn:li:activity:1", offset=40, limit=20)
    args, kwargs = c.session.request.call_args
    assert args[1].endswith(f"/acc/posts/urn%3Ali%3Aactivity%3A1/{what}")
    assert kwargs["params"] == {"offset": 40, "limit": 20}
    assert out["items"] == [{"id": "a"}]


@pytest.mark.parametrize("meth,what", [("list_comments", "comments"),
                                       ("list_reactions", "reactions")])
def test_comment_id_vise_le_commentaire(meth, what):
    c = _client()
    c.session.request.return_value = _resp(body={"data": []})
    getattr(c, meth)("p1", comment_id="c9")
    args, kwargs = c.session.request.call_args
    assert args[1].endswith(f"/acc/posts/p1/comments/c9/{what}")
    assert kwargs["params"] == {}


def test_429_lit_l_en_tete_retry_after():
    c = _client()
    c.session.request.return_value = _resp(
        429, body={"detail": "We only allow 10 requests."}, headers={"Retry-After": "7"})
    with pytest.raises(UnipileRateLimited) as e:
        c.list_reactions("p1")
    assert e.value.retry_after == 7


def test_429_sans_en_tete_lit_le_corps():
    c = _client()
    c.session.request.return_value = _resp(
        429, body={"detail": "We only allow 10 requests. Retry in 3 seconds."})
    with pytest.raises(UnipileRateLimited) as e:
        c.list_comments("p1")
    assert e.value.retry_after == 3
