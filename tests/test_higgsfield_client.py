"""Contract of the Higgsfield client (`Authorization: Key <id>:<secret>`; one path per
model; asynchronous jobs read back by request id).

Mocks `requests.Session.request`: paths, bodies and headers as in the vendor's API
reference, the key in the header only, a fresh idempotency key per submission, the
202 with no body of a cancel, local refusals, and `price_of` on both shapes of an
estimate answer (an amount, or a sentence of per-second prices).
"""
from __future__ import annotations

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.higgsfield import client as hf

KEY = "KEY_ID:KEY_SECRET"
RID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"
SEEDANCE = ("For 16:9 video without video input, your request costs roughly $0.2056 per second of generated "
            "video at 480p, $0.4622 at 720p, and $1.1372 at 1080p. Each 1,000 video tokens costs $0.0214 at "
            "480p or 720p and $0.0234 at 1080p.")


class _Resp:
    def __init__(self, status_code=200, body=None, content=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content if content is not None else b"{}"
        self.text = str(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"responses": [], "calls": []}

    def fake_request(self, method, url, **kwargs):
        seen["calls"].append((method, url, kwargs))
        seen.update(method=method, url=url, kwargs=kwargs)
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200, body={"status": "queued", "request_id": RID})

    monkeypatch.setattr(hf.requests.Session, "request", fake_request)
    return seen


@pytest.fixture()
def cli():
    return hf.HiggsfieldClient(api_key=KEY)


def test_key_is_required():
    with pytest.raises(MissingCredential):
        hf.HiggsfieldClient(api_key=None)


@pytest.mark.parametrize("key", ["just-a-secret", ":secret", "id:", "  :  "])
def test_key_must_be_id_colon_secret(key):
    with pytest.raises(ValueError, match="KEY_ID:KEY_SECRET"):
        hf.HiggsfieldClient(api_key=key)


def test_key_in_authorization_header_only(cli, capture):
    cli.list_models()
    assert cli.session.headers["Authorization"] == f"Key {KEY}"
    assert KEY not in capture["url"]
    assert "params" not in capture["kwargs"]


@pytest.mark.parametrize("call,method,path", [
    (lambda c: c.probe(), "GET", "/models"),
    (lambda c: c.list_models(), "GET", "/models"),
    (lambda c: c.get_request(RID), "GET", f"/requests/{RID}/status"),
    (lambda c: c.delete_request(RID), "DELETE", f"/requests/{RID}"),
    (lambda c: c.estimate("higgsfield-ai/soul/v2/standard", {"prompt": "a lamp"}),
     "POST", "/estimate/higgsfield-ai/soul/v2/standard"),
    (lambda c: c.generate("kling-video/v3.0/pro/image-to-video", {"prompt": "x"}),
     "POST", "/kling-video/v3.0/pro/image-to-video"),
    (lambda c: c.create_upload_url("image/png"), "POST", "/files/generate-upload-url"),
])
def test_paths(cli, capture, call, method, path):
    call(cli)
    assert capture["method"] == method
    assert capture["url"] == hf.BASE_URL + path
    assert capture["kwargs"]["timeout"] == (10, 60)


def test_generate_sends_the_params_as_body_with_a_fresh_idempotency_key(cli, capture):
    params = {"image_url": "https://cdn.example.com/a.png", "prompt": "slow push in", "duration": 5}
    first = cli.generate("bytedance/seedance-2.5/image-to-video", params)
    key1 = capture["kwargs"]["headers"]["Idempotency-Key"]
    cli.generate("bytedance/seedance-2.5/image-to-video", params)
    key2 = capture["kwargs"]["headers"]["Idempotency-Key"]
    assert capture["kwargs"]["json"] == params
    assert key1 != key2 and len(key1) == 36
    assert first["idempotency_key"] == key1
    assert "Hf-Expires-After" not in capture["kwargs"]["headers"]


def test_generate_keeps_a_given_idempotency_key_and_retention(cli, capture):
    cli.generate("z-image/turbo", {"prompt": "x"}, idempotency_key="take-12", expires_after=3600)
    assert capture["kwargs"]["headers"] == {"Idempotency-Key": "take-12", "Hf-Expires-After": "3600"}


@pytest.mark.parametrize("kwargs", [{"expires_after": 600}, {"expires_after": True},
                                    {"idempotency_key": ""}, {"idempotency_key": "has space"}])
def test_generate_refuses_bad_headers_before_any_call(cli, capture, kwargs):
    with pytest.raises(ValueError):
        cli.generate("z-image/turbo", {"prompt": "x"}, **kwargs)
    assert capture["calls"] == []


def test_a_submission_is_never_retried(cli, capture):
    capture["responses"].append(_Resp(503, body={"detail": {"code": "admission_control_unavailable"}}))
    with pytest.raises(UpstreamHTTPError):
        cli.generate("z-image/turbo", {"prompt": "x"})
    assert len(capture["calls"]) == 1


@pytest.mark.parametrize("model", ["", "requests/abc/status", "files/generate-upload-url", "estimate/x",
                                   "models", "../models", "a/../b", "Kling/V3", "a//b", "https://evil.example/x"])
def test_model_slug_is_checked(cli, capture, model):
    with pytest.raises(ValueError, match="model slug"):
        cli.generate(model, {"prompt": "x"})
    assert capture["calls"] == []


def test_model_slug_trims_slashes():
    assert hf.model_path("/higgsfield-ai/soul/v2/standard/") == "higgsfield-ai/soul/v2/standard"
    assert hf.model_path("soul-id") == "soul-id"


def test_params_must_be_an_object(cli, capture):
    with pytest.raises(ValueError, match="params"):
        cli.estimate("z-image/turbo", ["prompt"])


@pytest.mark.parametrize("rid", ["", "123", f"{RID}/../x", RID + "0"])
def test_request_id_is_a_uuid(cli, capture, rid):
    with pytest.raises(ValueError, match="request_id"):
        cli.get_request(rid)
    assert capture["calls"] == []


def test_cancel_answers_202_with_no_body(cli, capture):
    capture["responses"].append(_Resp(202, content=b""))
    assert cli.cancel_request(RID) == {"request_id": RID, "status": "canceled"}
    assert capture["method"] == "POST" and capture["url"].endswith(f"/requests/{RID}/cancel")


def test_cancel_of_a_started_job_raises(cli, capture):
    capture["responses"].append(_Resp(400, body={"detail": "Request already started"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.cancel_request(RID)
    assert e.value.status_code == 400


def test_upload_url_checks_the_content_type(cli, capture):
    cli.create_upload_url("video/mp4")
    assert capture["kwargs"]["json"] == {"content_type": "video/mp4"}
    with pytest.raises(ValueError, match="content_type"):
        cli.create_upload_url("application/pdf")


def test_upstream_errors_keep_status_and_detail(cli, capture):
    capture["responses"].append(_Resp(404, body={"detail": "model_not_found"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.estimate("nope/nope", {"prompt": "x"})
    assert e.value.status_code == 404 and "model_not_found" in str(e.value.body)


# --- price_of ----------------------------------------------------------------

def test_price_of_an_amount():
    assert hf.price_of({"type": "estimate", "credits": "0.050", "usd": "0.004"}, {}) == {
        "usd": 0.004, "approximate": False, "basis": "the estimate's usd"}


def test_price_of_a_per_second_sentence_at_the_asked_resolution():
    p = hf.price_of({"type": "description", "pricing_description": SEEDANCE},
                    {"duration": 5, "resolution": "720p"})
    assert p["usd"] == pytest.approx(2.311) and p["approximate"] is True
    assert "720p" in p["basis"]


def test_price_of_a_sentence_without_resolution_takes_the_highest_rate():
    p = hf.price_of({"pricing_description": SEEDANCE}, {"duration": 4})
    assert p["usd"] == pytest.approx(4.5488) and "1080p" in p["basis"]


@pytest.mark.parametrize("params,why", [
    ({"resolution": "720p"}, "duration"),
    ({"duration": "5", "resolution": "720p"}, "duration"),
    ({"duration": True}, "duration"),
    ({"duration": 5, "resolution": "4k"}, "no rate for resolution"),
    ({"duration": 5, "video_url": "https://cdn.example.com/in.mp4"}, "video"),
    ({"duration": 5, "reference_video_urls": ["https://cdn.example.com/in.mp4"]}, "video"),
])
def test_price_of_a_sentence_says_when_it_cannot(params, why):
    p = hf.price_of({"pricing_description": SEEDANCE}, params)
    assert p["usd"] is None and why in p["basis"]


@pytest.mark.parametrize("estimate", [None, [], {}, {"usd": "n/a"}, {"pricing_description": "Contact sales"}])
def test_price_of_nothing_readable(estimate):
    assert hf.price_of(estimate, {"duration": 5})["usd"] is None
