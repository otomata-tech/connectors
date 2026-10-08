"""Per-token pacing of the Pennylane Firm API: 5 requests per second per token.

Time is driven by an injected clock: the fake `sleep` advances it, so no test
waits for real except `test_real_clock_paces_the_calls`, about 0.1 s.
"""

import threading
import time

import pytest

from oto.tools.pennylane_firm import PennylaneFirmClient
from oto.tools.pennylane_firm.rate_limit import TokenRateLimiter, token_fingerprint


class _FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(t):
    return TokenRateLimiter(5, 1.0, clock=t.clock, sleep=t.sleep)


def test_the_sixth_call_waits_for_the_next_second():
    t = _FakeTime()
    lim = _limiter(t)
    for _ in range(5):
        lim.acquire("a")
    assert t.sleeps == []
    lim.acquire("a")
    assert t.sleeps == [1.0]


def test_the_window_slides():
    t = _FakeTime()
    lim = _limiter(t)
    for _ in range(5):
        lim.acquire("a")
        t.now += 0.1           # five calls over 0.5 s
    lim.acquire("a")           # at 100.5: the first slot frees at 101.0
    assert t.sleeps == [pytest.approx(0.5)]


def test_two_tokens_do_not_block_each_other():
    t = _FakeTime()
    lim = _limiter(t)
    for _ in range(5):
        lim.acquire("a")
        lim.acquire("b")
    assert t.sleeps == []


def test_reservations_queue_in_order():
    t = _FakeTime()
    lim = _limiter(t)
    waits = [lim.reserve("a") for _ in range(12)]   # nobody sleeps yet
    assert waits == [0.0] * 5 + [1.0] * 5 + [2.0] * 2


def test_the_key_is_a_fingerprint_not_the_token():
    t = _FakeTime()
    lim = _limiter(t)
    lim.acquire("secret-token")
    assert list(lim._slots) == [token_fingerprint("secret-token")]
    assert "secret-token" not in repr(lim._slots)


def test_concurrent_reservations_lose_no_slot():
    """Threads reserving at once (clock frozen): every slot is handed out once,
    five per second, as if the calls had come one by one."""
    t = _FakeTime()
    lim = _limiter(t)
    waits = []
    lock = threading.Lock()
    start = threading.Barrier(5)

    def worker():
        start.wait()
        for _ in range(3):
            w = lim.reserve("a")
            with lock:
                waits.append(w)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(waits) == [0.0] * 5 + [1.0] * 5 + [2.0] * 5


def test_real_clock_paces_the_calls():
    lim = TokenRateLimiter(2, 0.05)
    began = time.monotonic()
    for _ in range(5):
        lim.acquire("a")
    assert time.monotonic() - began >= 0.1 - 0.005


def test_clients_share_the_process_wide_limiter(monkeypatch):
    """A client per call: the pacing must still be the token's, not the instance's."""
    t = _FakeTime()
    shared = _limiter(t)
    from oto.tools.pennylane_firm import client as client_module
    monkeypatch.setattr(client_module, "SHARED_LIMITER", shared)

    class _Ok:
        status_code = 200
        content = b"{}"
        text = "{}"
        headers = {}

        def json(self):
            return {}

    for _ in range(6):
        c = PennylaneFirmClient(token="tok")
        c.session.request = lambda *a, **kw: _Ok()
        c.get_company(1)
    assert t.sleeps == [1.0]
