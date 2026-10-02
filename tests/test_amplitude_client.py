"""Amplitude client contract (Basic auth key pair, region host, JSON params).

Mocks `requests.Session.get`: checks the key pair and region, the encoding of
the JSON query parameters (funnel steps repeat `e`, in order), date
normalisation, path escaping, error typing, and the ABSENCE of any write.
"""
from __future__ import annotations

import json

import pytest

from oto.tools.amplitude import client as amp
from oto.tools.common.errors import UpstreamHTTPError


class _Resp:
    def __init__(self, status_code: int, body, text=None):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = text if text is not None else json.dumps(body)
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": []}

    def fake_get(self, url, params=None, **kwargs):
        seen["calls"].append({"url": url, "params": params, "auth": self.auth})
        seen.update(url=url, params=params, auth=self.auth)
        return seen.get("resp") or _Resp(200, {"data": {}})

    monkeypatch.setattr(amp.requests.Session, "get", fake_get)
    return seen


def _client(**kw):
    return amp.AmplitudeClient("k", "s", **kw)


def _p(seen):
    return dict(seen["params"] or [])


# --- credentials and region ---------------------------------------------------

def test_secret_key_is_required(monkeypatch):
    monkeypatch.delenv("AMPLITUDE_SECRET_KEY", raising=False)
    with pytest.raises(Exception):
        amp.AmplitudeClient("k", None)


def test_key_pair_sent_as_basic_auth(capture):
    _client().list_event_types()
    assert capture["auth"] == ("k", "s")
    assert capture["url"] == "https://amplitude.com/api/2/taxonomy/event"


def test_eu_region_picks_the_eu_host(capture):
    _client(region=" EU ").list_cohorts()
    assert capture["url"] == "https://analytics.eu.amplitude.com/api/3/cohorts"


def test_unknown_region_is_refused():
    with pytest.raises(ValueError, match="region"):
        _client(region="asia")


# --- dates and JSON params ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("2026-09-01", "20260901"),
                                          ("20260901", "20260901")])
def test_day_accepts_both_formats(raw, expected):
    assert amp.day(raw) == expected


def test_day_refuses_garbage():
    with pytest.raises(ValueError):
        amp.day("last week")


def test_segmentation_encodes_json_params(capture):
    _client().segmentation({"event_type": "Sign Up"}, "2026-09-01", "2026-09-07",
                           metric="totals", interval=7,
                           segments=[{"prop": "country", "op": "is",
                                      "values": ["France"]}],
                           group_by="gp:plan", limit=10)
    p = _p(capture)
    assert capture["url"].endswith("/api/2/events/segmentation")
    assert json.loads(p["e"]) == {"event_type": "Sign Up"}
    assert json.loads(p["s"]) == [{"prop": "country", "op": "is", "values": ["France"]}]
    assert (p["start"], p["end"], p["m"], p["i"], p["g"], p["limit"]) == (
        "20260901", "20260907", "totals", 7, "gp:plan", 10)
    assert "e2" not in p and "formula" not in p   # None never reaches the querystring


def test_funnel_repeats_e_in_step_order(capture):
    steps = [{"event_type": "A"}, {"event_type": "B"}, {"event_type": "C"}]
    _client().funnel(steps, "20260901", "20260930", mode="ordered",
                     conversion_window_seconds=86400)
    es = [json.loads(v) for k, v in capture["params"] if k == "e"]
    assert es == steps
    assert _p(capture)["cs"] == 86400


def test_funnel_needs_two_steps():
    with pytest.raises(ValueError, match="two steps"):
        _client().funnel([{"event_type": "A"}], "20260901", "20260930")


def test_retention_uses_se_and_re(capture):
    _client().retention({"event_type": "Sign Up"}, {"event_type": "_active"},
                        "20260901", "20260930", mode="rolling")
    p = _p(capture)
    assert json.loads(p["se"]) == {"event_type": "Sign Up"}
    assert json.loads(p["re"]) == {"event_type": "_active"}
    assert p["rm"] == "rolling"


def test_sessions_kind_is_closed():
    with pytest.raises(ValueError):
        _client().sessions("median", "20260901", "20260930")


# --- paths -------------------------------------------------------------------

def test_chart_id_is_escaped(capture):
    _client().chart_query("ab/../c")
    assert capture["url"] == "https://amplitude.com/api/3/chart/ab%2F..%2Fc/query"


def test_dot_segment_is_refused():
    with pytest.raises(ValueError):
        _client().chart_query("..")


def test_chart_csv_returns_text(capture):
    capture["resp"] = _Resp(200, None, text="a,b\n1,2\n")
    assert _client().chart_csv("abc") == "a,b\n1,2\n"


def test_cohort_download_flow_paths(capture):
    c = _client()
    c.request_cohort("coh1", props=True)
    c.cohort_status("req1")
    c.cohort_file("req1")
    urls = [x["url"] for x in capture["calls"]]
    assert urls == ["https://amplitude.com/api/5/cohorts/request/coh1",
                    "https://amplitude.com/api/5/cohorts/request-status/req1",
                    "https://amplitude.com/api/5/cohorts/request/req1/file"]
    assert dict(capture["calls"][0]["params"])["props"] == 1


# --- errors ------------------------------------------------------------------

def test_403_is_typed_with_its_body(capture):
    body = {"error": {"http_code": 403, "type": "unspecified",
                      "metadata": {"details": "Invalid API/Secret Key combination"}}}
    capture["resp"] = _Resp(403, body)
    with pytest.raises(UpstreamHTTPError) as e:
        _client().list_event_types()
    assert e.value.status_code == 403
    assert e.value.body == body


# --- read only ---------------------------------------------------------------

def test_no_write_method_exists():
    """Ingestion, taxonomy writes and cohort upload are absent ON PURPOSE —
    wiring one back needs a PR here, not a line in a tool."""
    for name in ("track", "identify", "upload_cohort", "update_cohort_membership",
                 "create_event_type", "delete_event_type", "export"):
        assert not hasattr(amp.AmplitudeClient, name)
    assert not any(n.startswith(("post", "put", "delete", "_post"))
                   for n in dir(amp.AmplitudeClient))
