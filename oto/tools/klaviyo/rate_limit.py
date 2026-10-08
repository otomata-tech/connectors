"""Per-account, per-endpoint request pacing for the Klaviyo API.

Klaviyo limits each endpoint per account with two windows: a burst (requests
per second) and a steady rate (requests per minute). The figures below are
those of the API reference for revision 2026-07-15. A private key belongs to
one account, so the pacing state is keyed by a SHA-256 fingerprint of the key
(never the key itself) and by endpoint, and lives at module level: the
consumer builds a client per call, often from a thread pool.

Each call reserves, under a lock, the earliest start that keeps every bucket
it counts against within both windows, then sleeps until that start outside
the lock. A wait longer than `max_wait_s` is refused instead of slept: the
caller gets a retryable `KlaviyoRateLimited` and nothing is reserved. The
daily cap of the values reports (225 per day) is not paced: Klaviyo's 429
reports it.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Iterable, Tuple

from .errors import KlaviyoRateLimited

BURST_SECONDS = 1.0
STEADY_SECONDS = 60.0
DEFAULT_MAX_WAIT_S = 30.0

_XS = (1, 15)
_S = (3, 60)
_M = (10, 150)
_L = (75, 750)
_XL = (350, 3500)
_REPORT = (1, 2)

#: bucket -> (burst per second, steady per minute)
ENDPOINT_LIMITS: Dict[str, Tuple[int, int]] = {
    "get_account": _XS,
    "list_profiles": _L,
    "get_profile": _L,
    "create_or_update_profile": _L,
    "list_lists": _L,
    "get_list": _L,
    "list_profiles_in_list": _L,
    "add_profiles_to_list": _M,
    "remove_profiles_from_list": _M,
    "list_segments": _L,
    "get_segment": _L,
    "list_profiles_in_segment": _L,
    "list_campaigns": _M,
    "get_campaign": _M,
    "list_flows": _S,
    "get_flow": _S,
    "list_metrics": _M,
    "get_metric": _M,
    "list_events": _XL,
    "create_event": _XL,
    "query_metric_aggregates": _S,
    "query_campaign_values": _REPORT,
    "query_flow_values": _REPORT,
    "subscribe_profiles": _L,
    "unsubscribe_profiles": _L,
    # additional-fields[list|segment]=profile_count counts here as well.
    "list_profile_count": _XS,
    "segment_profile_count": _XS,
}


def key_fingerprint(api_key: str) -> str:
    """Stable key for a private key: its SHA-256 digest, never the key."""
    return hashlib.sha256(f"klaviyo|{api_key}".encode()).hexdigest()


class EndpointRateLimiter:
    """Burst and steady windows per (key fingerprint, bucket).

    `clock` (monotonic seconds) and `sleep` are injectable so that tests can
    drive time without waiting.
    """

    def __init__(self, limits: Dict[str, Tuple[int, int]] = None, *,
                 max_wait_s: float = DEFAULT_MAX_WAIT_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.limits = dict(ENDPOINT_LIMITS if limits is None else limits)
        for bucket, (burst, steady) in self.limits.items():
            if burst < 1 or steady < 1:
                raise ValueError(f"rate limit of {bucket!r} needs burst and "
                                 "steady >= 1")
        self.max_wait_s = max_wait_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        # (fingerprint, bucket) -> start times of the last `steady` slots
        self._slots: Dict[Tuple[str, str], Deque[float]] = {}

    def _earliest(self, slots: Deque[float], burst: int, steady: int,
                  now: float) -> float:
        start = max(now, slots[-1]) if slots else now
        if len(slots) >= burst:
            start = max(start, slots[-burst] + BURST_SECONDS)
        if len(slots) >= steady:
            start = max(start, slots[-steady] + STEADY_SECONDS)
        return start

    def reserve(self, api_key: str, buckets: Iterable[str]) -> float:
        """Reserve the next slot for `api_key` in every bucket and return how
        long to wait for it; refuse (reserving nothing) beyond `max_wait_s`."""
        fingerprint = key_fingerprint(api_key)
        names = list(buckets)
        unknown = [b for b in names if b not in self.limits]
        if unknown:
            raise ValueError(f"no rate limit for bucket(s) {unknown}")
        with self._lock:
            now = self._clock()
            queues = []
            start = now
            for bucket in names:
                burst, steady = self.limits[bucket]
                slots = self._slots.setdefault((fingerprint, bucket),
                                               deque(maxlen=steady))
                queues.append(slots)
                start = max(start, self._earliest(slots, burst, steady, now))
            wait = start - now
            if wait > self.max_wait_s:
                raise KlaviyoRateLimited(
                    429, "rate_limited",
                    f"Klaviyo paces {', '.join(names)} per account: the next "
                    f"slot opens in {wait:.0f} s, beyond the {self.max_wait_s:g} s "
                    "this client waits. Retry later.",
                    retry_after=wait, local=True)
            for slots in queues:
                slots.append(start)
            return wait

    def acquire(self, api_key: str, buckets: Iterable[str]) -> None:
        """Block until `api_key` may send one more request on these buckets."""
        wait = self.reserve(api_key, buckets)
        if wait > 0:
            self._sleep(wait)


# Shared by every client of the process: the limits are the account's.
SHARED_LIMITER = EndpointRateLimiter()
