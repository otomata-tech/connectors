"""Minimal Firebase Realtime Database WebSocket client.

⚠️ **The WebSocket is not an optimisation — it is the only transport that works
here.** There is no simpler one to fall back to: do not "simplify" this module by
rewriting it over plain HTTP.

The database is sharded across three families (master, per-business, per-calendar):
the classmethods below build each one, and `calendar_shard_index` computes the
calendar one rather than looking it up.

Large messages arrive multi-frame, in two variants — both handled by `_recv_raw`.

Reads can be BOUNDED (`limit_last`, `limit_first`, `range_on`) — see `get`. A node
here holds years of a business's history, and reading it whole is not a slow read,
it is a read that does not finish.

⚠️ **The upstream drops a connection that carries no traffic, silently and fast.**
Measured 2026-09-09, two connections opened to the same host in the same second:
the one read every 15 s was still open after 135 s; the one left idle was gone at
45 s, with no close frame. Our own keepalive ping is what NOTICES it — the error
surfaces as `sent 1011 (internal error) keepalive ping timeout`, which reads like a
fault of ours and is in fact the report of a peer that left.

A pooled connection is idle between two tool calls by nature: a conversation does
not tick every 30 s. So a connection here is **expected to die**, and `get` re-opens
it rather than failing. That is not a retry that masks a fault; it is the normal
life of this transport. Removing the ping would only remove the DETECTION.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import websockets


def calendar_shard_index(calendar_id: str) -> int:
    """Planity's hash function for calendar shards (returns 1..4)."""
    return sum(ord(c) for c in calendar_id) % 4 + 1


def limit_last(n: int, index: str = ".key") -> dict:
    """The last `n` children by `index` — the newest slice of a growing node."""
    return _limite(n, index, "r")


def limit_first(n: int, index: str = ".key") -> dict:
    """The first `n` children by `index`."""
    return _limite(n, index, "l")


def _limite(n: int, index: str, depuis: str) -> dict:
    if not isinstance(n, int) or n <= 0:
        # A `0` returns an empty node that reads as "this salon has nothing", and
        # a negative goes onto the wire as is. We refuse here rather than explain
        # an absence later.
        raise ValueError(f"a read bound must be a positive integer, not {n!r}")
    return {"i": index, "l": n, "vf": depuis}


def range_on(index: str, start: Any, end: Any, limit: Optional[int] = None) -> dict:
    """Children whose `index` falls in [start, end], inclusive at both ends.

    ⚠️ **Firebase compares string indexes as STRINGS.** On an index that holds
    `"YYYY-MM-DD HH:MM"`, an `end` of `"YYYY-MM-DD"` matches nothing at all — every
    value of that day sorts after it — and the empty answer reads as "no
    appointments that day". The caller owns the end bound; `appointments.py` is
    where that particular one is computed, once.
    """
    if not index:
        raise ValueError("a range is read ON an index — one is required")
    q: dict = {"i": index, "sp": start, "ep": end}
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError(f"a read bound must be a positive integer, not {limit!r}")
        q["l"] = limit
        q["vf"] = "l"
    return q


class FirebaseRTDB:
    """One connection to one Firebase RTDB namespace."""

    def __init__(self, host: str, namespace: str, id_token: str, app_id: str):
        self.host = host
        self.namespace = namespace
        self.id_token = id_token
        # Planity's Firebase App ID — supplied by the caller, never hard-coded here
        # (see `config.PlanityEndpoints`). It goes out as `p=` in the handshake.
        self.app_id = app_id
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id = 0
        # Request TAG counter, distinct from the request counter: see `get`.
        self._tag = 0
        #: How many times this socket has been re-opened. Read by the tests, and
        #: useful in operations: a value that climbs fast says the peer cuts off
        #: earlier than we think, which no error log would tell us any more.
        self.reconnexions = 0
        # INSTANCE lock (not module-level): two concurrent reads that find the
        # socket dead must not open two connections, one of which would at once
        # be orphaned — open at the third party, never closed.
        self._ouverture = asyncio.Lock()

    @classmethod
    def master(cls, id_token: str, app_id: str) -> "FirebaseRTDB":
        return cls("planity-production.firebaseio.com", "planity-production",
                   id_token, app_id)

    @classmethod
    def business_shard(cls, shard_name: str, id_token: str,
                       app_id: str) -> "FirebaseRTDB":
        host = f"planity-production-{shard_name}.europe-west1.firebasedatabase.app"
        ns = f"planity-production-{shard_name}"
        return cls(host, ns, id_token, app_id)

    @classmethod
    def calendars_shard(cls, calendar_id: str, id_token: str,
                        app_id: str) -> "FirebaseRTDB":
        idx = calendar_shard_index(calendar_id)
        host = f"planity-production-calendars-{idx}.firebaseio.com"
        ns = f"planity-production-calendars-{idx}"
        return cls(host, ns, id_token, app_id)

    async def connect(self):
        uri = f"wss://{self.host}/.ws?v=5&p={self.app_id}&ns={self.namespace}"
        # Explicit bounds on opening and closing: a WebSocket that waits on a
        # third party with no maximum delay at ITS level holds the caller
        # indefinitely, even when the wrappers above believe they bounded it.
        self.ws = await websockets.connect(uri, max_size=32 * 1024 * 1024,
                                           open_timeout=15, close_timeout=5)
        # Swallow handshake (control msg)
        await self._recv_raw(timeout=10)
        # Authenticate
        await self._send_action("auth", {"cred": self.id_token})
        auth_reply = await self._recv_until_reply(self._req_id, timeout=10)
        if auth_reply.get("s") != "ok":
            raise RuntimeError(f"Firebase auth failed: {auth_reply}")

    def est_ouverte(self) -> bool:
        """Is the socket usable? **With no I/O at all.**

        We read `close_code`: `None` while the connection lives, set as soon as
        it dies (`1006` on a cut with no close frame — the case measured here).
        It is the most stable attribute across versions of the lib; the shapes of
        `state` have changed, this one has not."""
        return self.ws is not None and getattr(self.ws, "close_code", None) is None

    async def assurer_ouverte(self) -> None:
        """Re-opens the socket if it is dead. Costs nothing when it is alive."""
        if self.est_ouverte():
            return
        async with self._ouverture:
            # Re-test UNDER the lock: while we waited for it, another read may
            # have re-opened. Without this second test, it would be reconnected underneath.
            if self.est_ouverte():
                return
            ancien = self.ws
            self.ws = None
            if ancien is not None:
                try:
                    await ancien.close()
                except Exception:  # noqa: SILENT — socket already dead, we discard it
                    pass
            self.reconnexions += 1
            await self.connect()

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ─────────────────────── wire protocol ───────────────────────

    async def _recv_raw(self, timeout: float = 5.0) -> Optional[dict]:
        """Receive one logical message (handles Firebase's two multi-frame formats).

        Format A: '<count>\\n<first_part>' then (count-1) follow-up frames.
        Format B: '<count>' alone, then `count` follow-up frames.
        """
        first = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        # Multi-frame detection
        if first and first[:1].isdigit():
            if "\n" in first[:10]:
                # Format A
                count_str, _, rest = first.partition("\n")
                try:
                    count = int(count_str)
                    parts = [rest] if rest else []
                    while len(parts) < count:
                        parts.append(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
                    first = "".join(parts)
                except ValueError:
                    pass
            elif first.isdigit():
                # Format B: the whole frame is just the count
                try:
                    count = int(first)
                    parts: list[str] = []
                    while len(parts) < count:
                        parts.append(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
                    first = "".join(parts)
                except ValueError:
                    pass
        try:
            return json.loads(first)
        except (ValueError, TypeError):
            # A frame that is not JSON is NOT a failure: the protocol mixes
            # control frames with data frames, and the caller
            # (`_recv_until_reply`) ignores anything that is not a dict and keeps
            # waiting for ITS reply — until its own maximum delay, which is
            # what settles it for good.
            return None

    async def _send_action(self, action: str, body: dict) -> int:
        """Send a data frame. Returns req_id."""
        self._req_id += 1
        rid = self._req_id
        frame = {"t": "d", "d": {"r": rid, "a": action, "b": body}}
        await self.ws.send(json.dumps(frame))
        return rid

    async def _recv_until_reply(
        self,
        rid: int,
        timeout: float = 10.0,
        data_collector: Optional[dict] = None,
    ) -> dict:
        """Receive frames until we get the reply for rid.

        If data_collector is passed, intermediate data-push frames matching its
        'path' key are stored in data_collector['value'].
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"No reply for req {rid}")
            msg = await self._recv_raw(timeout=remaining)
            if not isinstance(msg, dict):
                continue
            d = msg.get("d")
            if not isinstance(d, dict):
                continue
            if d.get("a") == "d" and data_collector is not None:
                b = d.get("b", {})
                if isinstance(b, dict) and b.get("p") == data_collector.get("path"):
                    data_collector["value"] = b.get("d")
            if d.get("r") == rid:
                return d.get("b", {}) or {}

    # ─────────────────────── public API ───────────────────────

    async def get(self, path: str, query: Optional[dict] = None) -> Any:
        """Read a path once, whole or BOUNDED. Returns the value, or `None`.

        Pass `query` — built by `limit_last` / `limit_first` / `range_on` — to read
        a slice instead of the whole node. Without it, the read is unbounded, which
        on a node holding a business's history is not a slow read but one that does
        not finish.

        ⚠️ **A query REQUIRES a tag `t` in the frame.** Firebase answers a `q` frame
        that carries `q` without `t` with `internal_error` or `permission_denied` —
        a refusal that reads as a rights problem, and sends you looking at database
        rules for something that is a wire-format omission. The tag is emitted here
        and nowhere else, so no caller can forget it: it is what identifies the
        query listen on the connection, and it is per-connection, not per-request
        (two listens on the same path with different bounds must not share it).

        **The connection is re-opened when it has died**, before the read and, if
        the peer leaves mid-read, once more. The upstream drops idle connections
        within a minute (see the module docstring): a pooled connection is dead more
        often than alive, and failing on it would turn one silent hang-up into every
        subsequent call failing until the pool forgets the session.

        The retry is bounded to ONE and covers only a peer that left — never a
        refusal, a timeout, or a bad path. A read is idempotent, so replaying it
        costs a round trip; replaying anything else would be a fault masked.

        ⚠️ It opens a listen and does NOT close it — the connection is short-lived
        and dropped with the client, which is why that costs nothing here.

        Raises RuntimeError with the upstream status on failure.
        """
        await self.assurer_ouverte()
        try:
            return await self._interroger(path, query)
        except websockets.exceptions.ConnectionClosed:
            # The peer left DURING the read — the pre-check could not know.
            # A single retry, on a fresh socket.
            await self.assurer_ouverte()
            return await self._interroger(path, query)

    async def _interroger(self, path: str, query: Optional[dict]) -> Any:
        """One read pass on the current socket. Does not reconnect."""
        collector: dict = {"path": path, "value": None}
        body: dict = {"p": path, "h": ""}
        if query:
            self._tag += 1
            body["q"] = dict(query)
            body["t"] = self._tag
        rid = await self._send_action("q", body)
        reply = await self._recv_until_reply(rid, data_collector=collector)
        status = reply.get("s")
        if status != "ok":
            raise RuntimeError(f"Firebase '{path}' failed: {status} / {reply.get('d')}")
        # Data usually arrives via a data-push frame before the ack; fall back to
        # the reply body if nothing was pushed (e.g. empty/null value).
        if collector["value"] is not None:
            return collector["value"]
        return reply.get("d")
