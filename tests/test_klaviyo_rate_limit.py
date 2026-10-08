"""EndpointRateLimiter: burst and steady windows per key and endpoint."""

import pytest

from oto.tools.klaviyo import ENDPOINT_LIMITS, EndpointRateLimiter, KlaviyoRateLimited
from oto.tools.klaviyo.rate_limit import key_fingerprint


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _limiter(limits, max_wait_s=120.0):
    clock = _Clock()
    return EndpointRateLimiter(limits, max_wait_s=max_wait_s, clock=clock,
                               sleep=clock.sleep), clock


def test_burst_window_spaces_the_calls():
    limiter, clock = _limiter({"e": (2, 100)})
    waits = [limiter.reserve("k", ["e"]) for _ in range(5)]
    assert waits == [0.0, 0.0, 1.0, 1.0, 2.0]


def test_steady_window_holds_the_minute():
    limiter, clock = _limiter({"report": (1, 2)})
    assert limiter.reserve("k", ["report"]) == 0.0
    assert limiter.reserve("k", ["report"]) == 1.0
    assert limiter.reserve("k", ["report"]) == 60.0


def test_a_wait_beyond_the_bound_is_refused_and_reserves_nothing():
    limiter, clock = _limiter({"report": (1, 2)}, max_wait_s=30.0)
    limiter.acquire("k", ["report"])
    limiter.acquire("k", ["report"])
    with pytest.raises(KlaviyoRateLimited) as ei:
        limiter.acquire("k", ["report"])
    assert ei.value.local is True and ei.value.retryable is True
    assert ei.value.retry_after == pytest.approx(59.0)
    clock.now = 60.0
    assert limiter.reserve("k", ["report"]) == 0.0


def test_keys_and_endpoints_do_not_wait_for_each_other():
    limiter, _ = _limiter({"a": (1, 15), "b": (1, 15)})
    assert limiter.reserve("k1", ["a"]) == 0.0
    assert limiter.reserve("k2", ["a"]) == 0.0
    assert limiter.reserve("k1", ["b"]) == 0.0
    assert limiter.reserve("k1", ["a"]) == 1.0


def test_a_call_waits_for_its_strictest_bucket():
    limiter, _ = _limiter({"get_list": (75, 750), "list_profile_count": (1, 15)})
    assert limiter.reserve("k", ["get_list", "list_profile_count"]) == 0.0
    assert limiter.reserve("k", ["get_list"]) == 0.0
    assert limiter.reserve("k", ["get_list", "list_profile_count"]) == 1.0


def test_acquire_sleeps_the_reserved_wait():
    limiter, clock = _limiter({"e": (1, 60)})
    limiter.acquire("k", ["e"])
    limiter.acquire("k", ["e"])
    assert clock.slept == [1.0]


def test_unknown_bucket_is_an_error():
    limiter, _ = _limiter({"e": (1, 1)})
    with pytest.raises(ValueError):
        limiter.reserve("k", ["nope"])


def test_the_key_is_never_kept_in_clear():
    limiter, _ = _limiter({"e": (1, 1)})
    limiter.reserve("pk_secret", ["e"])
    assert all("pk_secret" not in fp for fp, _ in limiter._slots)
    assert (key_fingerprint("pk_secret"), "e") in limiter._slots


def test_every_described_function_has_a_limit():
    import pathlib

    import yaml
    path = pathlib.Path(__file__).resolve().parent.parent / "connectors" / "klaviyo" / "connector.yaml"
    names = {f["name"] for f in yaml.safe_load(path.read_text(encoding="utf-8"))["functions"]}
    assert names <= set(ENDPOINT_LIMITS)
