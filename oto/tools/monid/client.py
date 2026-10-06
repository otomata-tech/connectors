"""Monid — paid gateway to the data endpoints of many providers.

Monid (monid.ai) puts roughly 2,000 endpoints from about 70 providers (web search,
scraping, contact enrichment, social networks…) behind ONE API key, **billed per call
against a prepaid wallet** of the workspace. Auth is
`Authorization: Bearer <key>`; the key is bound to its workspace.

**Written against the OpenAPI contract `0.1.0`** published by Monid (`https://api.monid.ai`),
**not yet probed against a real account**: what follows is read from the contract.

The flow: `discover(q)` (semantic search, cards `{items, total}`) →
`inspect(provider, endpoint)` (the only place where the **input schema** and the
**current price** can be read) → `run(…)` (launches, and debits) → `get_run` / `wait_for_run` /
`list_runs` / `stop_run`. `wallet_balance()` says what is left.

⚠️ **`POST /v1/run` cannot be read from the HTTP code.** Its status MIRRORS the
provider's: a `COMPLETED` run whose provider answered 404 comes back as 404,
a timeout as 408 (`TIMED_OUT`), a provider's 402 as 502 — each time
with the WHOLE run in the body. The SHAPE of the body decides: `runId` +
`status` = a run, returned as is; the `{code, message}` envelope = a refusal from
Monid, raised as `MonidHTTPError`. A 402 from Monid ALWAYS concerns the wallet.

⚠️ **Run status ≠ provider status.** `COMPLETED` = "the provider
answered", whatever it answered (`providerResponse.httpStatus`); `FAILED` = failure
on Monid's side; `BLOCKED` (returned as 200) = a workspace cap (budget, number of
runs) refused the run before execution. Terminal states: `COMPLETED`, `FAILED`, `BLOCKED`,
`STOPPED`, `TIMED_OUT` — without the last two, a wait would run until its bound.

⚠️ **A run is NEVER retried**, nor is a stop: no idempotency key,
and replaying a launch lost in flight can pay twice. When the request may
have gone out without a usable response (read timeout, connection broken or never
established, unreadable body, 5xx), the outcome is UNKNOWN: the error carries
`may_have_run=True`, and the list of runs must be re-read before any
new attempt. Only reads (`GET`) are retried, on 429/5xx; a
`Retry-After` over 15 s is not slept on, it surfaces in `retry_after`; a
wallet 503 WITHOUT `Retry-After` (failed, or not yet created) surfaces immediately.

⚠️ **A run's input has three parts** — `body`, `queryParams`, `pathParams` —
where the `inspect` schema places them, never flattened. `endpoint` (which starts with
`/`) and `provider` (which may contain dots) pass as `discover` returns them.

**What is billed is READ, never recomputed** (`run_cost_usd`): `cost.value`
in dollars on a re-read run, `billing.reportedCost` in whole units on the launch
response, absent until the run is settled; `price` = the catalog price.

**Waiting is bounded**: `wait_for_run` stops at `max_wait_s` (cap 300 s) and
returns the LAST state read, terminal or not — never sleeping past the deadline.
**Redirects are never followed**: a 3xx would carry the authentication header away;
it is refused, except at launch when its body is a run (status copied).

**Deliberately absent**: budgets and run caps, resources, API key management,
wallet top-up and history, the deprecated route `/v1/discover`, the public
registry `/public/v1/*`, the workspace header (useless with a key), and
`X-Monid-Client`, not sent: the contract renders `hints` according to the declared client
(command for cli/api/mcp, structured target for web, omitted if unknown); without this
header, the Monid doc shows them as a curl command — unverified, the client does not rely on it.

Requires: requests
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from ..common.credentials import require
from ..common import UpstreamHTTPError

SERVICE = "monid"
DEFAULT_BASE_URL = "https://api.monid.ai"
_HTTP_TIMEOUT = (10, 30)  # (connect, read) — no unbounded wait
RUN_READ_TIMEOUT = 60  # launch read: a synchronous provider holds the connection
RUN_STATUSES = ("READY", "RUNNING", "STOPPING", "COMPLETED", "FAILED", "BLOCKED",
                "STOPPED", "TIMED_OUT")
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "BLOCKED", "STOPPED", "TIMED_OUT"})
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})  # reads only
_MAX_ATTEMPTS = 3
_RETRY_AFTER_MAX = 15  # beyond this, the delay goes back to the caller instead of being slept
_MAX_WAIT_S = 300      # cap on `wait_for_run`, whatever the caller asks

_CATEGORY_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # used with `fullmatch`: `$` allows a trailing `\n`
_ERROR_CODES = frozenset({  # `ApiErrorCode` registry of contract 0.1.0; outside the registry = absent
    "X402_WORKSPACE_RESOURCES", "X402_ACCRUING_COST", "X402_EXCEEDS_SETTLEMENT_WINDOW",
    "X402_VALIDITY_WINDOW_TOO_SHORT"})
_COST_DIVISORS = {"MICRO_DOLLAR": 1_000_000, "CENT": 100, "DOLLAR": 1}
_NOT_JSON = object()


class MonidHTTPError(UpstreamHTTPError):
    """Refusal from Monid. Keeps `.status_code` / `.body` (parsed envelope or text).

    `request_id` = the `x-request-id` header; `retry_after` = the `Retry-After` in
    seconds (429; 503 of a wallet being created — without it, a 503 from a failed
    or nonexistent wallet surfaces without a new attempt). `may_have_run` is true
    only on a 5xx from a run launch: nothing says the run was not created.
    """

    may_have_run: bool = False

    def __init__(self, status_code: int, body: Any = None, *,
                 request_id: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(status_code, body, service=SERVICE)
        self.request_id = request_id
        self.retry_after = retry_after
        detail = self.upstream_message
        if detail is None:
            detail = body if isinstance(body, (dict, list)) else _excerpt(body)
        # The parent's message copies the whole body, intermediary HTML page included.
        self.args = (f"{SERVICE} HTTP {status_code}: {detail}",)

    @property
    def error_code(self) -> Optional[str]:
        """`errorCode` of the envelope if it is in the contract registry; a value outside the
        registry counts as absent (the contract requires it) and stays readable in `.body`."""
        code = self.body.get("errorCode") if isinstance(self.body, dict) else None
        return code if isinstance(code, str) and code in _ERROR_CODES else None

    @property
    def upstream_message(self) -> Optional[str]:
        """Monid's human-readable message: `message`, otherwise `error.message`."""
        if not isinstance(self.body, dict):
            return None
        message = self.body.get("message")
        if not (isinstance(message, str) and message.strip()):
            nested = self.body.get("error")
            message = nested.get("message") if isinstance(nested, dict) else None
        return message if isinstance(message, str) and message.strip() else None


class MonidProtocolError(RuntimeError):
    """Unusable response (redirect, non-JSON 2xx, read lost on a run…).

    A transport or configuration fault, not a business refusal. `may_have_run`: a run's
    request may have gone out without a readable result — the run may exist, and be billed.
    """

    def __init__(self, message: str, *, request_id: Optional[str] = None,
                 may_have_run: bool = False):
        super().__init__(message)
        self.request_id = request_id
        self.may_have_run = may_have_run


def is_terminal(run: dict) -> bool:
    """Is the run in a final state? Case-sensitive comparison; a
    `status` that is not a string is not final (and does not raise)."""
    status = run.get("status") if isinstance(run, dict) else None
    return isinstance(status, str) and status in TERMINAL_STATUSES


def run_cost_usd(run: dict) -> Optional[float]:
    """What the run cost, in dollars — READ from the response, never recomputed.

    `cost.value` (dollars, re-read run), otherwise `billing.reportedCost` converted by
    its unit (launch response), otherwise `None`: not yet settled.
    """
    if not isinstance(run, dict):
        return None
    cost = run.get("cost")
    if (isinstance(cost, dict) and _is_number(cost.get("value"))
            and cost.get("currency") in (None, "USD")):
        return float(cost["value"])
    billing = run.get("billing")
    reported = billing.get("reportedCost") if isinstance(billing, dict) else None
    if (isinstance(reported, dict) and _is_number(reported.get("value"))
            and reported.get("currency") in (None, "USD")
            and reported.get("unit") in _COST_DIVISORS):
        return reported["value"] / _COST_DIVISORS[reported["unit"]]
    return None


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _excerpt(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text if len(text) <= 300 else text[:300] + "…"


def _non_empty_str(value: Any, name: str) -> str:
    """Returned AS IS: an endpoint or a provider is never rewritten."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{name}` must be a non-empty string (got {value!r}).")
    return value


def _run_path(run_id: Any) -> str:
    """A run identifier goes into the PATH: escaped, `/` included. `quote` leaves
    the dot alone, and `.`/`..` would become segments resolved to another route."""
    if _non_empty_str(run_id, "run_id") in (".", ".."):
        raise ValueError(f"`run_id` cannot be the path segment {run_id!r}.")
    return f"/v1/runs/{quote(run_id, safe='')}"


def _retry_after(resp: Any) -> Optional[float]:
    """`Retry-After` in seconds; `None` if missing or not readable as a number."""
    try:
        value = float(resp.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return max(0.0, value) if math.isfinite(value) else None


def _parse(resp: Any) -> Any:
    """The body's JSON, or `_NOT_JSON` — never a `ValueError`, which would be confused
    downstream with an argument validation refusal."""
    if not resp.content:
        return _NOT_JSON
    try:
        return resp.json()
    except ValueError:
        return _NOT_JSON


def _is_wallet_unavailable(resp: Any) -> bool:
    """A 503 `WalletUnavailableError`: `walletStatus` PRESENT, `null` included (not created)."""
    data = _parse(resp) if resp.status_code == 503 else None
    return isinstance(data, dict) and "walletStatus" in data


def _unknown_outcome(why: str) -> str:
    return (f"Unknown outcome of the launch: {why}. The run may exist and be "
            "billed — re-read the list of runs before any new attempt, "
            "relaunching may pay twice.")


class MonidClient:
    """Client of the Monid `/v1` API, Bearer auth by workspace API key."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """`api_key`: Monid API key;
        `base_url`: API root (default `https://api.monid.ai`)."""
        self.api_key = require(api_key, "MONID_API_KEY")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        # Key in a HEADER only: in the query string it would enter the URL,
        # hence the message of any exception and the access logs.
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}",
                                     "Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _send(self, method: str, path: str, *, params: Optional[Dict[str, Any]],
              body: Any, timeout: Any, retry: bool) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = None
        for attempt in range(_MAX_ATTEMPTS):
            resp = self.session.request(
                method, f"{self.base_url}{path}", params=clean or None, json=body,
                timeout=timeout, allow_redirects=False)
            if (not retry or resp.status_code not in _RETRY_STATUSES
                    or attempt == _MAX_ATTEMPTS - 1):
                break
            wait = _retry_after(resp)
            if wait is None:
                if resp.headers.get("Retry-After") is None and _is_wallet_unavailable(resp):
                    break  # wallet FAILED or nonexistent: retrying fixes nothing
                wait = float(2 ** attempt)
            elif wait > _RETRY_AFTER_MAX:
                break
            time.sleep(wait)
        return resp

    def _redirect_error(self, resp: Any, method: str, path: str) -> MonidProtocolError:
        return MonidProtocolError(
            f"Monid answered with a redirect (HTTP {resp.status_code}) on "
            f"{method} {path}, which is not followed: the base address "
            f"{self.base_url!r} does not point to the API.",
            request_id=resp.headers.get("x-request-id"))

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None, body: Any = None,
                 timeout: Any = _HTTP_TIMEOUT, retry: Optional[bool] = None) -> Any:
        """Common transport. `retry` defaults to "reads only" (GET)."""
        if retry is None:
            retry = method == "GET"
        resp = self._send(method, path, params=params, body=body, timeout=timeout, retry=retry)
        status, request_id = resp.status_code, resp.headers.get("x-request-id")
        if 300 <= status < 400:
            raise self._redirect_error(resp, method, path)
        data = _parse(resp)
        if status >= 400:
            raise MonidHTTPError(status, resp.text if data is _NOT_JSON else data,
                                 request_id=request_id, retry_after=_retry_after(resp))
        if not resp.content:
            return {}
        if data is _NOT_JSON:
            raise MonidProtocolError(
                f"Monid answered {status} without a JSON body (Content-Type "
                f"{resp.headers.get('Content-Type')!r}) on {method} {path}.",
                request_id=request_id)
        return data

    # --- identity, wallet ---------------------------------------------------

    def whoami(self) -> dict:
        """GET /v1/auth/whoami — `{user, workspace?}`, free: the key probe.
        **401** = key missing, malformed or revoked; **403** = no workspace."""
        return self._request("GET", "/v1/auth/whoami")

    def wallet_balance(self) -> dict:
        """GET /v1/wallet/balance — `{balance, held}`, amounts in dollars.

        `balance` is the SPENDABLE amount (live minus held), may be negative; `held` is
        reserved for running runs. **503** with `Retry-After` = wallet being created,
        retried within the bound; WITHOUT = failed or not yet created, raised immediately.
        """
        return self._request("GET", "/v1/wallet/balance")

    # --- catalog ------------------------------------------------------------

    def discover(self, q: str, *, limit: Optional[int] = None,
                 category: Optional[str] = None,
                 min_score: Optional[float] = None) -> dict:
        """POST /v1/discover/endpoints — semantic search of endpoints.

        Returns `{items, total}`: each card carries `provider`, `endpoint`,
        `displayName`, `displayDescription`, `price`, `tags`, `categories`; `total`
        counts the results above the floor BEFORE `limit`, no cursor.
        `category` = category identifier; `min_score` = relevance
        floor (0 to 2). The price that counts is the one from `inspect`.
        """
        if not isinstance(q, str) or not 1 <= len(q.strip()) <= 1000:
            raise ValueError("`q` must be a text of 1 to 1000 characters.")
        body: Dict[str, Any] = {"q": q.strip()}
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError(f"`limit` must be an integer ≥ 1 (got {limit!r}).")
            body["limit"] = limit
        if category is not None:
            if (not isinstance(category, str) or len(category) > 60
                    or not _CATEGORY_RE.fullmatch(category)):
                raise ValueError("`category` must be an identifier in lowercase letters, digits "
                                 f"and hyphens, 60 characters at most (got {category!r}).")
            body["category"] = category
        if min_score is not None:
            if not _is_number(min_score) or not 0 <= min_score <= 2:
                raise ValueError("`min_score` must be a number between 0 and 2 "
                                 f"(got {min_score!r}).")
            body["minScore"] = min_score
        return self._request("POST", "/v1/discover/endpoints", body=body)

    def inspect(self, provider: str, endpoint: str) -> dict:
        """POST /v1/inspect — the full card of an endpoint.

        The only place where the input schema (`input`: `pathParams`,
        `queryParams`, `body` as JSON Schema, and `bodyType`) and the current price (`price`,
        sometimes absent) can be read, with `notes`, `metrics`, `docUrl`. `provider` and `endpoint`
        pass AS `discover` returned them. **404** = endpoint unknown to this provider.
        """
        return self._request("POST", "/v1/inspect", body={
            "provider": _non_empty_str(provider, "provider"),
            "endpoint": _non_empty_str(endpoint, "endpoint")})

    # --- runs ---------------------------------------------------------------

    def run(self, provider: str, endpoint: str, *, body: Optional[dict] = None,
            query_params: Optional[dict] = None, path_params: Optional[dict] = None,
            timeout: float = RUN_READ_TIMEOUT) -> dict:
        """POST /v1/run — launches an endpoint and DEBITS the wallet. Never retried.

        The input goes out in three parts (`input.body`, `input.queryParams`,
        `input.pathParams`), only those that are not empty; `input` is
        omitted when all three are. `timeout` = READ budget in seconds.

        Returns the run as Monid sends it back as soon as the body is one (`runId` +
        `status`), WHATEVER the HTTP code: 200 (`COMPLETED`, `BLOCKED`), 202
        (accepted, to follow), 408 (`TIMED_OUT`), 502 (the provider answered 402),
        or the provider's code copied, 3xx included (redirect not followed).

        Raises `MonidHTTPError` on Monid's error envelope (402 = insufficient
        wallet; `may_have_run` true on a 5xx); `MonidProtocolError` on a 3xx
        without a run, and with `may_have_run=True` when the request may have gone out without
        a usable response (read lost, connection broken or never established, unreadable body).
        A CONNECT timeout surfaces as is: nothing went out.
        """
        payload: Dict[str, Any] = {"provider": _non_empty_str(provider, "provider"),
                                   "endpoint": _non_empty_str(endpoint, "endpoint")}
        parts: Dict[str, Any] = {}
        for key, name, value in (("body", "body", body),
                                 ("queryParams", "query_params", query_params),
                                 ("pathParams", "path_params", path_params)):
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"`{name}` must be an object (got {type(value).__name__}).")
            if value:
                parts[key] = value
        if parts:
            payload["input"] = parts
        if not _is_number(timeout) or timeout <= 0:
            raise ValueError(f"`timeout` must be a number of seconds > 0 (got {timeout!r}).")
        try:
            resp = self._send("POST", "/v1/run", params=None, body=payload,
                              timeout=(10, timeout), retry=False)
        except requests.exceptions.ConnectTimeout:
            raise
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError) as exc:
            # a refused connection is not a `ConnectTimeout`: "may have gone out"
            raise MonidProtocolError(_unknown_outcome(
                f"no usable response ({type(exc).__name__}), the request may have "
                "gone out"), may_have_run=True) from exc
        status, request_id = resp.status_code, resp.headers.get("x-request-id")
        data = _parse(resp)
        if isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k]
                                          for k in ("runId", "status")):
            return data  # before the 3xx: a copied provider status is still a run
        if 300 <= status < 400:
            raise self._redirect_error(resp, "POST", "/v1/run")
        if data is not _NOT_JSON and status >= 400:
            err = MonidHTTPError(status, data, request_id=request_id,
                                 retry_after=_retry_after(resp))
            err.may_have_run = status >= 500
            raise err
        why = (f"HTTP {status} without a run identifier" if data is not _NOT_JSON else
               f"HTTP {status} without a JSON body (Content-Type "
               f"{resp.headers.get('Content-Type')!r})")
        raise MonidProtocolError(_unknown_outcome(why), request_id=request_id,
                                 may_have_run=True)

    def get_run(self, run_id: str) -> dict:
        """GET /v1/runs/{runId} — the run's state, `input` and `output` included: `cost.value`
        (dollars) once settled, `stoppable` while it runs, `reason`/`controls` on a
        `BLOCKED`. **403** = run of another workspace; **404** = unknown run."""
        return self._request("GET", _run_path(run_id))

    def wait_for_run(self, run_id: str, *, max_wait_s: float, poll_initial: float = 1.0,
                     poll_max: float = 5.0) -> dict:
        """Re-reads the run until a final state or until `max_wait_s`, whichever comes first.

        Returns the LAST state read, terminal or not: a run still going at the deadline
        is not an error. First read immediate, then an interval starting at
        `poll_initial`, ×1.5 each round up to `poll_max`, never beyond
        the deadline. No retry (the loop serves as one): an HTTP error surfaces.
        """
        path = _run_path(run_id)
        if not _is_number(max_wait_s) or not 0 <= max_wait_s <= _MAX_WAIT_S:
            raise ValueError(f"`max_wait_s` must be between 0 and {_MAX_WAIT_S} "
                             f"seconds (got {max_wait_s!r}).")
        if (not _is_number(poll_initial) or not _is_number(poll_max)
                or not 0 < poll_initial <= poll_max):
            raise ValueError("it requires 0 < `poll_initial` ≤ `poll_max` (got "
                             f"{poll_initial!r}, {poll_max!r}).")
        deadline = time.monotonic() + max_wait_s
        interval = float(poll_initial)
        while True:
            remaining = deadline - time.monotonic()
            run = self._request("GET", path, retry=False,
                                timeout=(5, max(5.0, min(30.0, remaining))))
            now = time.monotonic()
            if is_terminal(run) or now >= deadline:
                return run
            time.sleep(min(interval, deadline - now))
            interval = min(interval * 1.5, float(poll_max))

    def list_runs(self, *, limit: Optional[int] = None, cursor: Optional[str] = None,
                  status: Optional[str] = None) -> dict:
        """GET /v1/runs — the workspace's runs, newest to oldest.

        Returns `{items, cursor?}`, `cursor` absent on the last page. `limit` from 1 to 100;
        `status` in any case, sent in uppercase. Rows carry neither `input` nor
        `output`: `get_run` returns them.
        """
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                                  or not 1 <= limit <= 100):
            raise ValueError(f"`limit` must be an integer from 1 to 100 (got {limit!r}).")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("`cursor` must be the string returned by the previous "
                             f"page (got {cursor!r}).")
        if status is not None:
            wanted = status.strip().upper() if isinstance(status, str) else None
            if wanted not in RUN_STATUSES:
                raise ValueError(f"Invalid `status`: {status!r}. Accepted values: "
                                 + ", ".join(RUN_STATUSES))
            status = wanted
        return self._request("GET", "/v1/runs", params={
            "limit": limit, "cursor": cursor or None, "status": status})

    def stop_run(self, run_id: str) -> dict:
        """POST /v1/runs/{runId}/stop — requests a stop. Never retried.

        **202** `{runId, status: "STOPPING", message}`: the stop is ASYNCHRONOUS, the run
        then goes `STOPPED` — or `COMPLETED` for a usage-billed endpoint, which
        settles what it consumed. **409** = run already finished or not stoppable.
        """
        return self._request("POST", f"{_run_path(run_id)}/stop", retry=False)


__all__ = ["DEFAULT_BASE_URL", "MonidClient", "MonidHTTPError", "MonidProtocolError",
           "RUN_READ_TIMEOUT", "RUN_STATUSES", "SERVICE", "TERMINAL_STATUSES",
           "is_terminal", "run_cost_usd"]
