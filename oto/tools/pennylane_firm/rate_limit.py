"""Per-token request pacing for the Pennylane Firm API (5 requests per second).

The limit belongs to the token, not to a client instance: the consumer builds
a client per call, often from a thread pool, so the pacing state lives at
module level, keyed by a SHA-256 fingerprint of the token (never the token
itself), and is guarded by locks.

Each call reserves the next free slot of a sliding window under the lock, then
sleeps until that slot outside the lock: callers on one token are served in
reservation order, and callers on different tokens never wait for each other.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict

REQUESTS_PER_WINDOW = 5
WINDOW_SECONDS = 1.0


def token_fingerprint(token: str) -> str:
    """Stable key for a token: its SHA-256 digest, never the token in clear."""
    return hashlib.sha256(f"pennylane_firm|{token}".encode()).hexdigest()


class TokenRateLimiter:
    """At most `requests` calls per `seconds`, per token fingerprint.

    `clock` (monotonic seconds) and `sleep` are injectable so that tests can
    drive time without waiting.
    """

    def __init__(self, requests: int = REQUESTS_PER_WINDOW,
                 seconds: float = WINDOW_SECONDS, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if requests < 1 or seconds <= 0:
            raise ValueError("rate limit needs requests >= 1 and seconds > 0")
        self.requests = requests
        self.seconds = seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        # fingerprint -> start times of the last `requests` reserved slots
        self._slots: Dict[str, Deque[float]] = {}

    def reserve(self, token: str) -> float:
        """Reserve the next slot for `token` and return how long to wait for it."""
        key = token_fingerprint(token)
        with self._lock:
            now = self._clock()
            slots = self._slots.setdefault(key, deque(maxlen=self.requests))
            start = now
            if slots:
                start = max(start, slots[-1])
            if len(slots) == self.requests:
                start = max(start, slots[0] + self.seconds)
            slots.append(start)
            return start - now

    def acquire(self, token: str) -> None:
        """Block until `token` may send one more request."""
        wait = self.reserve(token)
        if wait > 0:
            self._sleep(wait)


# Shared by every client of the process: the limit is the token's.
SHARED_LIMITER = TokenRateLimiter()
