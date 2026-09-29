"""Jev client contract (System One / Decisions, OpenRouter Bearer).

Mocks `requests.Session.post`: URL, request body, default model, typed upstream errors,
and the rubric guard that refuses a question without `criteria` before any call.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.jev import client as jv

NOUL = {"type": "noul", "instructions": "Does the condition hold?",
        "criteria": {"true": "yes", "false": "no"}}


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = str(body)
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {}

    def fake_post(self, url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return _Resp(200, {"answers": {"q": {"type": "noul", "noul": 0.9}},
                           "model": jv.DEFAULT_MODEL,
                           "usage": {"input_tokens": 341, "cost": 1.4322e-05}})

    monkeypatch.setattr(jv.requests.Session, "post", fake_post)
    return seen


def _client(**kw):
    return jv.JevClient(api_key="sk-or-test", **kw)


def test_auth_header_is_bearer():
    assert _client().session.headers["Authorization"] == "Bearer sk-or-test"


def test_decide_posts_to_systemone_with_dated_snapshot(capture):
    _client().decide({"a": "b"}, {"q": NOUL})
    assert capture["url"] == "https://openrouter.ai/api/v1/systemone"
    body = capture["kwargs"]["json"]
    assert body == {"model": jv.DEFAULT_MODEL, "state": {"a": "b"}, "questions": {"q": NOUL}}
    # The DATED snapshot, not the bare id: a calibrated threshold must not drift.
    assert jv.DEFAULT_MODEL == "typesafe/jev-1.13-20260917"


def test_base_url_override_for_direct_typesafe(capture):
    _client(base_url="https://api.typesafe.ai", path="/v1/systemone").decide({"a": "b"}, {"q": NOUL})
    assert capture["url"] == "https://api.typesafe.ai/v1/systemone"


def test_explicit_model_wins(capture):
    _client().decide({"a": "b"}, {"q": NOUL}, model="~typesafe/jev-latest")
    assert capture["kwargs"]["json"]["model"] == "~typesafe/jev-latest"


def test_upstream_error_is_typed(monkeypatch):
    monkeypatch.setattr(jv.requests.Session, "post",
                        lambda self, url, **kw: _Resp(400, {"error": {"message": "nope"}}))
    with pytest.raises(UpstreamHTTPError) as e:
        _client().decide({"a": "b"}, {"q": NOUL})
    assert e.value.status_code == 400 and e.value.is_client_error


@pytest.mark.parametrize("questions, expected", [
    ({}, "at least one question"),
    ({"q": {"type": "bool", "instructions": "?", "criteria": {"true": "x", "false": "y"}}}, "unknown type"),
    ({"q": {"type": "noul", "instructions": "", "criteria": {"true": "x", "false": "y"}}}, "instructions"),
    # ⚠️ The case the guard exists for: a question without `criteria`.
    ({"q": {"type": "noul", "instructions": "?"}}, "criteria"),
    ({"q": {"type": "choice", "instructions": "?", "criteria": {"only": "one"}}}, "criteria"),
    ({"q": {"type": "score", "instructions": "?", "criteria": {"not": "a list"}}}, "LIST"),
])
def test_malformed_rubric_refused_before_call(questions, expected):
    with pytest.raises(ValueError, match=expected):
        _client().decide({"a": "b"}, questions)


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5.5", "openai/gpt-6-sol",
                                   "z-ai/glm-5.3-flashx", "mistral/mistral-large-2512"])
def test_other_publisher_model_refused(model, capture):
    """Refused client-side: no billed round trip, and the key never reaches a text model."""
    with pytest.raises(ValueError, match="decision models"):
        _client().decide({"a": "b"}, {"q": NOUL}, model=model)
    assert not capture


def test_guard_checks_publisher_not_catalog(capture):
    """Any model from the same publisher passes: a per-model list would go stale."""
    _client().decide({"a": "b"}, {"q": NOUL}, model="typesafe/jev-router")
    assert capture["kwargs"]["json"]["model"] == "typesafe/jev-router"


def test_typesafe_alias_passes(capture):
    _client().decide({"a": "b"}, {"q": NOUL}, model="~typesafe/jev-latest")
    assert capture["kwargs"]["json"]["model"] == "~typesafe/jev-latest"


def test_valid_rubric_passes(capture):
    r = _client().decide({"a": "b"}, {
        "n": NOUL,
        "c": {"type": "choice", "instructions": "?", "criteria": {"a": "x", "b": "y"}},
        "s": {"type": "score", "instructions": "?", "criteria": ["low", "medium", "high"]}})
    assert r["usage"]["cost"] == 1.4322e-05
