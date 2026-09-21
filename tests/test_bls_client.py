"""Contrat du client BLS (OEWS) — sans réseau.

Mocke `requests.Session.post` : identifiants de série pour les trois types de zone,
normalisation du SOC (suffixe O*NET compris), découpage au plafond de 25 séries,
lignes « No Data Available » (pas des erreurs), valeurs `-` et notes, refus rendu
en HTTP 200.
"""
from __future__ import annotations

import pytest

from oto.tools.bls import client as bls
from oto.tools.bls.areas import resolve_area
from oto.tools.common.errors import UpstreamHTTPError


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


def _point(value, year="2025", footnotes=None):
    return {"year": year, "period": "A01", "periodName": "Annual", "latest": "true",
            "value": value, "footnotes": footnotes or [{}]}


@pytest.fixture()
def api(monkeypatch):
    """Faux amont : rend `values[série]` (défaut "100") pour chaque série demandée."""
    state = {"calls": [], "values": {}, "messages": [], "status": "REQUEST_SUCCEEDED",
             "http": 200}

    def fake_post(self, url, **kwargs):
        ids = kwargs["json"]["seriesid"]
        state["calls"].append({"url": url, "kwargs": kwargs})
        series = []
        for sid in ids:
            v = state["values"].get(sid, _point("100"))
            series.append({"seriesID": sid, "data": [v] if v else []})
        return _Resp(state["http"], {"status": state["status"],
                                     "message": state["messages"],
                                     "Results": {"series": series}})

    monkeypatch.setattr(bls.requests.Session, "post", fake_post)
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    monkeypatch.setattr(bls, "get_secret", lambda name, default=None: None)
    return state


def test_series_id_three_area_types():
    assert bls.oews_series_id("151299", "N", "0000000", "15") == "OEUN000000000000015129915"
    il = resolve_area("Illinois")
    assert bls.oews_series_id("151299", il["area_type"], il["area_code"], "15") == \
        "OEUS170000000000015129915"
    chi = resolve_area("16980")
    assert bls.oews_series_id("151299", chi["area_type"], chi["area_code"], "13") == \
        "OEUM001698000000015129913"


@pytest.mark.parametrize("raw,expected", [
    ("15-1299", ("151299", None)), ("151299", ("151299", None)),
    (" 15-1299.08 ", ("151299", "08")), ("15-1299.00", ("151299", "00")),
])
def test_normalize_soc(raw, expected):
    assert bls.normalize_soc(raw) == expected


@pytest.mark.parametrize("bad", ["", "15-129", "software developer", "15-1299.8"])
def test_normalize_soc_refuses(bad):
    with pytest.raises(ValueError):
        bls.normalize_soc(bad)


@pytest.mark.parametrize("raw,code,kind", [
    ("US", "0000000", "N"), ("national", "0000000", "N"),
    ("il", "1700000", "S"), ("New  York", "3600000", "S"), ("DC", "1100000", "S"),
    ("16980", "0016980", "M"),
])
def test_resolve_area(raw, code, kind):
    area = resolve_area(raw)
    assert (area["area_code"], area["area_type"]) == (code, kind)


def test_resolve_area_refuses_metro_name():
    with pytest.raises(ValueError, match="CBSA"):
        resolve_area("Chicago")


def test_one_request_for_three_areas_no_key_in_body(api):
    out = bls.BLSClient().oews_wages("15-1299", ["US", "IL", "16980"])
    assert len(api["calls"]) == 1 and out["requests"] == 1
    call = api["calls"][0]
    assert call["url"] == "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    assert len(call["kwargs"]["json"]["seriesid"]) == 21
    assert "registrationkey" not in call["kwargs"]["json"]
    assert "params" not in call["kwargs"]
    assert call["kwargs"]["timeout"] == (10, 30)


def test_chunks_over_25_series(api):
    out = bls.BLSClient().oews_wages("151299", ["US", "IL", "CA", "NY", "TX"])
    sizes = [len(c["kwargs"]["json"]["seriesid"]) for c in api["calls"]]
    assert sizes == [21, 14] and out["requests"] == 2
    assert [a["area"] for a in out["areas"]] == ["US", "Illinois", "California",
                                                 "New York", "Texas"]


def test_key_goes_in_body_and_raises_cap(api):
    c = bls.BLSClient(registration_key="k")
    c.oews_wages("151299", ["US", "IL", "CA", "NY", "TX"])
    assert len(api["calls"]) == 1
    assert api["calls"][0]["kwargs"]["json"]["registrationkey"] == "k"


def test_get_series_refuses_over_cap(api):
    with pytest.raises(ValueError, match="plafond 25"):
        bls.BLSClient().get_series([f"S{i}" for i in range(26)])
    assert api["calls"] == []


def test_no_data_lines_are_not_errors(api):
    p90 = "OEUN000000000000015129915"
    api["values"][p90] = _point("188470")
    api["messages"] = [f"No Data Available for Series {p90} Year: 2023",
                       f"No Data Available for Series {p90} Year: 2024"]
    out = bls.BLSClient().oews_wages("15-1299.08", ["US"])
    assert out["messages"] == []
    assert out["onet_suffix_stripped"] == "08" and out["soc"] == "15-1299"
    assert out["year"] == "2025"
    assert out["areas"][0]["percentiles"]["p90"] == 188470


def test_missing_series_is_surfaced(api):
    med = "OEUM001698000000015129913"
    api["values"][med] = None
    api["messages"] = [f"Series does not exist for Series {med}"]
    area = bls.BLSClient().oews_wages("151299", ["16980"])
    assert area["messages"] == [f"Series does not exist for Series {med}"]
    assert area["areas"][0]["missing"] == ["p50"]
    assert area["areas"][0]["percentiles"]["p50"] is None


def test_dash_and_footnotes_are_not_coerced(api):
    p90 = "OEUN000000000000011101115"
    api["values"][p90] = _point("-", footnotes=[
        {"code": "5", "text": "This wage is equal to or greater than $115.00 per hour "
                              "or $239,200 per year."}])
    area = bls.BLSClient().oews_wages("11-1011", ["US"])["areas"][0]
    assert area["percentiles"]["p90"] is None
    assert area["raw"] == {"p90": "-"}
    assert area["footnotes"] == [{"measure": "p90", "code": "5",
                                  "text": "This wage is equal to or greater than "
                                          "$115.00 per hour or $239,200 per year."}]
    assert area["missing"] == []


def test_refusal_in_http_200_body(api):
    api["status"] = "REQUEST_NOT_PROCESSED"
    api["messages"] = ["Request could not be serviced, as the daily threshold for total "
                       "number of requests allocated to the user has been reached."]
    with pytest.raises(bls.BLSRequestError) as e:
        bls.BLSClient().oews_wages("151299", ["US"])
    assert e.value.status == "REQUEST_NOT_PROCESSED" and "daily threshold" in str(e.value)


def test_http_error_is_typed(api):
    api["http"] = 503
    with pytest.raises(UpstreamHTTPError) as e:
        bls.BLSClient().get_series(["OEUN000000000000015129915"])
    assert e.value.status_code == 503 and e.value.service == "bls"
