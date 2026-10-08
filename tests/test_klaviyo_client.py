"""KlaviyoClient: headers, paths, queries, JSON:API bodies, paging, refusals.

No network: the client's session is replaced by a recorder that returns
prepared responses, so the transport (`_send`) is exercised as in use. The
pacing has its own tests (`test_klaviyo_rate_limit.py`).
"""

import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.klaviyo import (EndpointRateLimiter, KlaviyoClient, KlaviyoError,
                               KlaviyoPaginationError, KlaviyoRateLimited,
                               next_cursor)

BASE = "https://a.klaviyo.com/api"
PROFILE = "01HZX8A6R0J3VQ2N9P4K7T5M1B"


class _Response:
    def __init__(self, status_code=200, json_body=None, text=None, headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text if text is not None else (
            json.dumps(json_body) if json_body is not None else "")
        self.content = self.text.encode()
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no JSON")
        return self._json


class _Session:
    """Records every request and answers the prepared responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class _CountingLimiter(EndpointRateLimiter):
    def __init__(self):
        super().__init__()
        self.acquired = []

    def acquire(self, api_key, buckets):
        self.acquired.append((api_key, list(buckets)))


def _client(*responses):
    limiter = _CountingLimiter()
    c = KlaviyoClient(api_key="pk_test", rate_limiter=limiter,
                      session=_Session(*responses))
    return c, c.session, limiter


def _body(call):
    return json.loads(call["data"])


# --- Construction and headers ------------------------------------------------


def test_key_is_required_and_sent_in_the_klaviyo_header():
    with pytest.raises(MissingCredential) as ei:
        KlaviyoClient(api_key="")
    assert ei.value.name == "KLAVIYO_API_KEY"
    c, s, limiter = _client(_Response(json_body={"data": [{"type": "account", "id": "AbC123"}]}))
    assert s.headers["Authorization"] == "Klaviyo-API-Key pk_test"
    assert s.headers["revision"] == "2026-07-15"
    assert s.headers["Accept"] == "application/vnd.api+json"
    assert c.get_account()["data"][0]["id"] == "AbC123"
    call = s.calls[0]
    assert (call["method"], call["url"]) == ("GET", f"{BASE}/accounts")
    assert "pk_test" not in json.dumps(call["params"])
    assert call["allow_redirects"] is False
    assert limiter.acquired == [("pk_test", ["get_account"])]


# --- Reads ---------------------------------------------------------------------


def test_list_profiles_query_names_and_comma_lists():
    c, s, _ = _client(_Response(json_body={"data": [], "links": {"next": None}}))
    c.list_profiles(filter='equals(email,"jane@example.com")', sort="-updated",
                    page_size=100, page_cursor="abc",
                    additional_fields=["subscriptions", "predictive_analytics"])
    call = s.calls[0]
    assert call["url"] == f"{BASE}/profiles"
    assert call["params"] == {
        "filter": 'equals(email,"jane@example.com")', "sort": "-updated",
        "page[size]": 100, "page[cursor]": "abc",
        "additional-fields[profile]": "subscriptions,predictive_analytics"}
    assert call["data"] is None and call["headers"] is None


def test_unset_arguments_stay_out_of_the_query():
    c, s, _ = _client(_Response(json_body={"data": []}))
    c.list_lists()
    assert s.calls[0]["params"] == {}


@pytest.mark.parametrize("method, args, path, params, bucket", [
    ("get_profile", (PROFILE,), f"/profiles/{PROFILE}", {}, "get_profile"),
    ("get_list", ("Y6nRLr",), "/lists/Y6nRLr", {}, "get_list"),
    ("list_profiles_in_list", ("Y6nRLr",), "/lists/Y6nRLr/profiles", {}, "list_profiles_in_list"),
    ("list_segments", (), "/segments", {}, "list_segments"),
    ("get_segment", ("Xy12Ab",), "/segments/Xy12Ab", {}, "get_segment"),
    ("list_profiles_in_segment", ("Xy12Ab",), "/segments/Xy12Ab/profiles", {}, "list_profiles_in_segment"),
    ("get_campaign", ("01J0CAMP",), "/campaigns/01J0CAMP", {}, "get_campaign"),
    ("list_flows", (), "/flows", {}, "list_flows"),
    ("get_flow", ("Ab12Cd",), "/flows/Ab12Cd", {}, "get_flow"),
    ("list_metrics", (), "/metrics", {}, "list_metrics"),
    ("get_metric", ("UxxK4u",), "/metrics/UxxK4u", {}, "get_metric"),
    ("list_events", (), "/events", {}, "list_events"),
])
def test_read_paths_and_buckets(method, args, path, params, bucket):
    c, s, limiter = _client(_Response(json_body={"data": {}}))
    getattr(c, method)(*args)
    assert (s.calls[0]["method"], s.calls[0]["url"]) == ("GET", f"{BASE}{path}")
    assert s.calls[0]["params"] == params
    assert limiter.acquired == [("pk_test", [bucket])]


def test_get_profile_include_and_extra_fields():
    c, s, _ = _client(_Response(json_body={"data": {"id": PROFILE}}))
    c.get_profile(PROFILE, additional_fields=["subscriptions"], include=["lists", "segments"])
    assert s.calls[0]["params"] == {"additional-fields[profile]": "subscriptions",
                                    "include": "lists,segments"}


def test_profile_count_counts_against_its_own_bucket_too():
    c, s, limiter = _client(_Response(json_body={"data": {}}),
                            _Response(json_body={"data": {}}))
    c.get_list("Y6nRLr", additional_fields=["profile_count"])
    c.get_segment("Xy12Ab", additional_fields=["profile_count"])
    assert s.calls[0]["params"] == {"additional-fields[list]": "profile_count"}
    assert s.calls[1]["params"] == {"additional-fields[segment]": "profile_count"}
    assert limiter.acquired == [("pk_test", ["get_list", "list_profile_count"]),
                                ("pk_test", ["get_segment", "segment_profile_count"])]


def test_list_campaigns_requires_the_channel_in_the_filter():
    c, s, _ = _client(_Response(json_body={"data": []}))
    with pytest.raises(ValueError, match="messages.channel"):
        c.list_campaigns("equals(status,'Sent')")
    assert s.calls == []
    c.list_campaigns("equals(messages.channel,'email'),equals(status,'Sent')",
                     sort="-scheduled_at", include=["campaign-messages"])
    assert s.calls[0]["url"] == f"{BASE}/campaigns"
    assert s.calls[0]["params"]["include"] == "campaign-messages"


@pytest.mark.parametrize("bad", ["../accounts", "a/b", "", "Y6n Lr", None])
def test_path_ids_are_letters_and_digits(bad):
    c, s, _ = _client()
    with pytest.raises(ValueError):
        c.get_list(bad)
    assert s.calls == []


# --- POST reads ------------------------------------------------------------------


def test_reports_and_aggregates_post_json_api_bodies():
    report = {"type": "campaign-values-report", "attributes": {
        "statistics": ["open_rate"], "timeframe": {"key": "last_30_days"},
        "conversion_metric_id": "UxxK4u"}}
    aggregate = {"type": "metric-aggregate", "attributes": {
        "metric_id": "UxxK4u", "measurements": ["count"],
        "filter": ["greater-or-equal(datetime,2026-09-01T00:00:00)",
                   "less-than(datetime,2026-10-01T00:00:00)"]}}
    c, s, limiter = _client(*[_Response(json_body={"data": {}}) for _ in range(3)])
    c.query_campaign_values(report, page_cursor="cur")
    c.query_flow_values({**report, "type": "flow-values-report"})
    c.query_metric_aggregates(aggregate)
    assert [(x["method"], x["url"]) for x in s.calls] == [
        ("POST", f"{BASE}/campaign-values-reports"),
        ("POST", f"{BASE}/flow-values-reports"),
        ("POST", f"{BASE}/metric-aggregates")]
    assert s.calls[0]["params"] == {"page_cursor": "cur"}
    assert _body(s.calls[0]) == {"data": report}
    assert _body(s.calls[2]) == {"data": aggregate}
    assert s.calls[0]["headers"] == {"Content-Type": "application/vnd.api+json"}
    assert [b for _, b in limiter.acquired] == [
        ["query_campaign_values"], ["query_flow_values"], ["query_metric_aggregates"]]


def test_a_body_of_the_wrong_type_is_refused_before_sending():
    c, s, _ = _client()
    with pytest.raises(ValueError, match="campaign-values-report"):
        c.query_campaign_values({"type": "flow-values-report", "attributes": {}})
    with pytest.raises(ValueError):
        c.create_event({"type": "event"})
    assert s.calls == []


# --- Writes ------------------------------------------------------------------------


def test_create_or_update_profile_posts_the_resource():
    data = {"type": "profile", "attributes": {"email": "jane@example.com", "phone_number": None}}
    c, s, _ = _client(_Response(201, json_body={"data": {"type": "profile", "id": PROFILE}}))
    assert c.create_or_update_profile(data)["data"]["id"] == PROFILE
    call = s.calls[0]
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/profile-import")
    assert _body(call) == {"data": data}
    assert '"phone_number":null' in call["data"]


def test_list_membership_posts_and_deletes_profile_refs():
    refs = [{"type": "profile", "id": PROFILE}]
    c, s, _ = _client(_Response(204), _Response(204))
    assert c.add_profiles_to_list("Y6nRLr", refs) == {}
    assert c.remove_profiles_from_list("Y6nRLr", refs) == {}
    assert [(x["method"], x["url"]) for x in s.calls] == [
        ("POST", f"{BASE}/lists/Y6nRLr/relationships/profiles"),
        ("DELETE", f"{BASE}/lists/Y6nRLr/relationships/profiles")]
    assert _body(s.calls[1]) == {"data": refs}


@pytest.mark.parametrize("refs", [
    [], [{"type": "list", "id": "x"}], [{"type": "profile"}],
    [{"type": "profile", "id": "a/b"}],
    [{"type": "profile", "id": "a"}] * 1001,
])
def test_list_membership_refuses_bad_refs_locally(refs):
    c, s, _ = _client()
    with pytest.raises(ValueError):
        c.add_profiles_to_list("Y6nRLr", refs)
    assert s.calls == []


def test_create_event_is_accepted_without_a_body():
    event = {"type": "event", "attributes": {
        "properties": {}, "backfill": True,
        "metric": {"data": {"type": "metric", "attributes": {"name": "Placed Order"}}},
        "profile": {"data": {"type": "profile", "attributes": {"email": "jane@example.com"}}}}}
    c, s, _ = _client(_Response(202))
    assert c.create_event(event) == {}
    assert s.calls[0]["url"] == f"{BASE}/events" and _body(s.calls[0]) == {"data": event}


def _job(kind, consent, count):
    return {"type": kind, "attributes": {"profiles": {"data": [
        {"type": "profile", "attributes": {
            "email": f"p{i}@example.com",
            "subscriptions": {"email": {"marketing": {"consent": consent}}}}}
        for i in range(count)]}},
        "relationships": {"list": {"data": {"type": "list", "id": "Y6nRLr"}}}}


def test_subscribe_and_unsubscribe_post_consent_jobs():
    sub = _job("profile-subscription-bulk-create-job", "SUBSCRIBED", 2)
    unsub = _job("profile-subscription-bulk-delete-job", "UNSUBSCRIBED", 1)
    c, s, _ = _client(_Response(202), _Response(202))
    assert c.subscribe_profiles(sub) == {}
    assert c.unsubscribe_profiles(unsub) == {}
    assert [x["url"] for x in s.calls] == [
        f"{BASE}/profile-subscription-bulk-create-jobs",
        f"{BASE}/profile-subscription-bulk-delete-jobs"]
    assert _body(s.calls[0]) == {"data": sub}


def test_consent_jobs_are_bounded_locally():
    c, s, _ = _client()
    with pytest.raises(ValueError, match="1 to 100"):
        c.unsubscribe_profiles(_job("profile-subscription-bulk-delete-job", "UNSUBSCRIBED", 101))
    with pytest.raises(ValueError):
        c.subscribe_profiles(_job("profile-subscription-bulk-delete-job", "SUBSCRIBED", 1))
    assert s.calls == []


# --- Paging --------------------------------------------------------------------------


def _page(ids, next_url, included=()):
    return _Response(json_body={"data": [{"type": "profile", "id": i} for i in ids],
                                "included": list(included),
                                "links": {"next": next_url}})


def test_next_cursor_reads_links_next():
    assert next_cursor({"links": {"next": f"{BASE}/profiles/?page%5Bcursor%5D=bmV4dA"}}) == "bmV4dA"
    assert next_cursor({"links": {"next": f"{BASE}/campaign-values-reports?page_cursor=c2"}}) == "c2"
    assert next_cursor({"links": {"next": None}}) is None


def test_all_pages_follows_links_next():
    lst = {"type": "list", "id": "L1"}
    c, s, limiter = _client(
        _page(["a"], f"{BASE}/lists/Y6nRLr/profiles/?page%5Bcursor%5D=c2", [lst]),
        _page(["b"], f"{BASE}/lists/Y6nRLr/profiles/?page%5Bcursor%5D=c3", [lst]),
        _page(["c"], None))
    out = c.list_profiles_in_list("Y6nRLr", page_size=1, all_pages=True)
    assert [p["id"] for p in out["data"]] == ["a", "b", "c"]
    assert out["included"] == [lst] and out["pages"] == 3
    assert out["links"] == {"next": None}
    assert s.calls[0]["params"] == {"page[size]": 1}
    assert s.calls[1]["url"] == f"{BASE}/lists/Y6nRLr/profiles/?page%5Bcursor%5D=c2"
    assert s.calls[1]["params"] is None
    assert [b for _, b in limiter.acquired] == [["list_profiles_in_list"]] * 3


def test_all_pages_bound_keeps_links_next():
    pages = [_page([str(i)], f"{BASE}/profiles/?page%5Bcursor%5D=c{i + 1}") for i in range(3)]
    c, s, _ = _client(*pages)
    out = c.list_profiles(all_pages=True, max_pages=2)
    assert len(s.calls) == 2 and out["pages"] == 2
    assert next_cursor(out) == "c2"


@pytest.mark.parametrize("url", [
    "http://a.klaviyo.com/api/profiles/?page%5Bcursor%5D=x",
    "https://evil.example/api/profiles/?page%5Bcursor%5D=x",
    "https://a.klaviyo.com.evil.example/api/profiles",
    "https://user@a.klaviyo.com/api/profiles",
    "https://a.klaviyo.com:8443/api/profiles",
    "https://a.klaviyo.com/client/profiles",
])
def test_links_next_outside_the_klaviyo_api_is_not_followed(url):
    c, s, _ = _client(_page(["a"], url))
    with pytest.raises(KlaviyoPaginationError):
        c.list_profiles(all_pages=True)
    assert len(s.calls) == 1


# --- Refusals ------------------------------------------------------------------------


def _error(status, detail="bad", headers=None, pointer=None):
    error = {"id": "e1", "code": "x", "title": "t", "detail": detail}
    if pointer:
        error["source"] = {"pointer": pointer}
    return _Response(status, json_body={"errors": [error]}, headers=headers)


@pytest.mark.parametrize("status, code, retryable", [
    (400, "invalid_request", False), (401, "key_invalid", False),
    (403, "scope_missing", False), (409, "conflict", False),
    (500, "upstream_unavailable", True), (503, "upstream_unavailable", True),
])
def test_refusals_are_typed(status, code, retryable):
    c, _, _ = _client(_error(status))
    with pytest.raises(KlaviyoError) as ei:
        c.list_metrics()
    assert isinstance(ei.value, UpstreamHTTPError)
    assert (ei.value.status_code, ei.value.code, ei.value.retryable) == (status, code, retryable)


def test_function_refusals_override_the_table():
    c, _, _ = _client(_error(404), _error(409, "duplicate", pointer="/data/attributes/email"))
    with pytest.raises(KlaviyoError) as ei:
        c.get_profile(PROFILE)
    assert ei.value.code == "profile_not_found"
    with pytest.raises(KlaviyoError) as ei:
        c.create_or_update_profile({"type": "profile", "attributes": {"email": "x@example.com"}})
    assert ei.value.code == "profile_conflict"
    assert "duplicate (/data/attributes/email)" in str(ei.value)


def test_rate_limited_carries_retry_after():
    c, _, _ = _client(_error(429, "throttled", headers={"Retry-After": "12"}))
    with pytest.raises(KlaviyoRateLimited) as ei:
        c.list_flows()
    assert ei.value.retryable is True and ei.value.retry_after == 12.0
    assert ei.value.local is False


def test_redirect_is_not_followed():
    c, _, _ = _client(_Response(302, headers={"Location": "https://elsewhere.example"}))
    with pytest.raises(KlaviyoError) as ei:
        c.get_account()
    assert ei.value.code == "unexpected_redirect"
