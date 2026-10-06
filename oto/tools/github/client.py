"""GitHub REST API client — repositories, issues, pull requests, organizations, Actions.

REST API v3 (`https://api.github.com`, doc https://docs.github.com/en/rest), auth
**Bearer** (personal token, app installation token, or a workflow's
`GITHUB_TOKEN`). One method = one endpoint; bodies and responses pass through as-is.
Conventions noted from the vendor documentation on 2026-09-02.

This module carries **construction and transport**, and composes the call
families from `_api/` (repositories, issues, pull requests, organizations, Actions,
search). The constants live in `const.py` and are re-exported here.

Six things constrain the caller, and half of them are silent traps:

- ⚠️ **`per_page` caps at 100 and GitHub SILENTLY TRIMS anything above**: no
  error, just fewer rows than requested. A caller asking for 500 believes it
  has read everything and only has the first 100. `_check_per_page` therefore
  refuses locally, naming the limit.

- ⚠️ **Not every list is an array.** Search returns
  `{total_count, items: [...]}`, Actions returns `{total_count, workflow_runs: […]}`,
  `{jobs: […]}`, `{artifacts: […]}`. A pagination loop written for a bare
  array misses these endpoints, or worse, iterates over the dict's KEYS. `iterate()`
  writes the rule once: it follows the `Link` header and knows how to extract the list
  whatever its envelope.

- ⚠️ **Search is capped at 1,000 results**, regardless of
  pagination: `total_count` may announce 12,000 and the 11th page answer 422.
  `total_count` is therefore NOT the number of retrievable rows.

- ⚠️ **A 404 does not mean "does not exist".** On a private resource that
  the token is not allowed to see, GitHub answers 404 rather than 403, on purpose,
  so as not to disclose its existence. A "not found" private repository is most
  often a token *scope* problem, not a name problem.

- **Two usage limits, not one.** The primary one is described by the `x-ratelimit-*`
  headers. The "secondary" (anti-abuse) one hits bursts of writes
  and answers 403 or 429 with `Retry-After`. Both are retried here — **on
  reads only**: the REST API offers no idempotency key, and replaying a
  POST would create a second issue, a second comment, a second commit.

- **GitHub Enterprise Server** is reached through `base_url` (typically
  `https://<host>/api/v3`). The rest of the client is identical.

**Deliberately absent** (out of scope, not to be "completed" without a decision):
deleting a repository and deleting an organization — two destructive and
irreversible actions that no use case of this connector requires; management
of Actions secrets and variables (setting them through a connector would amount to
moving credentials from one vault to another); GitHub App administration;
and billing.

Requires: requests
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from ._api import (_ActionsMixin, _IssuesMixin, _OrgsMixin, _PullsMixin,
                   _ReposMixin, _SearchMixin)
from .const import (COLLABORATOR_PERMISSIONS, DEFAULT_ACCEPT,
                    DEFAULT_API_VERSION, DEFAULT_BASE_URL, DEFAULT_PER_PAGE,
                    HTTP_TIMEOUT, ISSUE_SORTS, ISSUE_STATE_WRITES,
                    ISSUE_STATES, MAX_ATTEMPTS, MAX_PER_PAGE, MEMBER_FILTERS,
                    MEMBERSHIP_ROLES, MERGE_METHODS, MIN_PER_PAGE,
                    ORG_REPO_TYPES, PULL_SORTS, PULL_STATES, REPO_SORTS,
                    REPO_TYPES, RETRY_STATUSES, REVIEW_EVENTS, RUN_STATUSES,
                    SEARCH_CODE_SORTS, SEARCH_ISSUE_SORTS, SEARCH_MAX_RESULTS,
                    SEARCH_REPO_SORTS, SORT_DIRECTIONS, TEAM_ROLES)

#: The keys under which GitHub files a list when the response is an OBJECT
#: and not an array. Written once: `iterate()` uses them to find the
#: rows without every caller having to know which envelope to expect.
_LIST_KEYS = ("items", "workflow_runs", "workflows", "jobs", "artifacts",
              "repositories", "check_runs", "check_suites", "secrets",
              "installations", "users", "commits")

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHubClient(
    _ReposMixin,
    _IssuesMixin,
    _PullsMixin,
    _OrgsMixin,
    _ActionsMixin,
    _SearchMixin,
):
    """GitHub REST v3 client (https://api.github.com), Bearer auth."""

    def __init__(self, token: Optional[str] = None,
                 base_url: Optional[str] = None,
                 api_version: Optional[str] = None):
        """
        Args:
            token: GitHub token. Personal token
                (classic or "fine-grained"), app installation token, or
                the ephemeral token of an Actions workflow.
            base_url: API root. Default `https://api.github.com`; for a
                GitHub Enterprise Server, `https://<host>/api/v3`.
            api_version: value of the `X-GitHub-Api-Version` header
                (default `DEFAULT_API_VERSION`).

        ⚠️ What the token CAN do depends on its scopes (classic token) or on its
        permissions and repository list (fine-grained token). A missing
        right often shows up as a **404**, not a 403: see the module
        header.
        """
        self.token = require(token, "GITHUB_TOKEN")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_version = api_version or DEFAULT_API_VERSION
        #: `Link` header of the last response (see `_request`/`iterate`).
        self._last_link: Optional[str] = None
        self.session = requests.Session()
        # Token in the HEADER only (never in the query string: it would end up in
        # the URL, hence in the message of any exception, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": DEFAULT_ACCEPT,
            "X-GitHub-Api-Version": self.api_version,
        })

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _check_per_page(per_page: Optional[int]) -> None:
        """`per_page` outside [1, 100] is refused HERE.

        ⚠️ GitHub would NOT return an error: it silently trims to 100. The
        local refusal is therefore the only way to tell "I have everything" from "I have
        the first hundred".
        """
        if per_page is None:
            return
        if not isinstance(per_page, int) or isinstance(per_page, bool):
            raise ValueError("`per_page` must be an integer.")
        if not (MIN_PER_PAGE <= per_page <= MAX_PER_PAGE):
            raise ValueError(
                f"`per_page` must be between {MIN_PER_PAGE} and {MAX_PER_PAGE} "
                f"(GitHub cap); got {per_page}. ⚠️ GitHub would trim to "
                f"{MAX_PER_PAGE} WITHOUT error — to go beyond, paginate with "
                "`page`, or loop with `iterate`.")

    @staticmethod
    def _check_choice(name: str, value: Optional[Any],
                      allowed: Iterable[Any]) -> None:
        """Locally refuse a value outside the enumeration, NAMING the valid ones."""
        if value is None:
            return
        allowed = tuple(allowed)
        if value not in allowed:
            raise ValueError(
                f"`{name}` invalid: {value!r}. Accepted values: "
                + ", ".join(repr(a) for a in allowed))

    @staticmethod
    def _encode_params(params: Optional[Dict[str, Any]]) -> List[Tuple[str, Any]]:
        """Params → pairs, `None` removed, booleans as `true`/`false`, lists
        joined by commas (the form GitHub reads for `labels`, `assignees`
        as a filter, etc.)."""
        out: List[Tuple[str, Any]] = []
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                out.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple)):
                out.append((key, ",".join(str(v) for v in value)))
            else:
                out.append((key, value))
        return out

    @staticmethod
    def _retry_after(resp: Any, attempt: int) -> float:
        """Delay before retrying.

        `Retry-After` first (upstream knows better than we do — it is notably what
        the anti-abuse secondary limit carries). Otherwise, if the primary limit
        is exhausted (`x-ratelimit-remaining: 0`), wait for the announced
        reset. Otherwise, exponential backoff.
        """
        headers = getattr(resp, "headers", None) or {}
        raw = headers.get("Retry-After")
        if raw:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
        if str(headers.get("x-ratelimit-remaining", "")).strip() == "0":
            reset = headers.get("x-ratelimit-reset")
            if reset:
                try:
                    # Bounded: a distant `reset` (or a skewed clock) must not
                    # freeze the caller for an hour.
                    return max(0.0, min(60.0, float(reset) - time.time()))
                except (TypeError, ValueError):
                    pass
        return float(2 ** attempt)

    def _is_retryable_status(self, resp: Any) -> bool:
        """403 is retryable ONLY if it carries the mark of a usage limit.

        GitHub serves the same code for "your token is not allowed" (final,
        retrying is useless) and for the anti-abuse secondary limit (transient).
        Telling them apart avoids hammering a missing permission three times.
        """
        status = resp.status_code
        if status in RETRY_STATUSES:
            return True
        if status != 403:
            return False
        headers = getattr(resp, "headers", None) or {}
        if headers.get("Retry-After"):
            return True
        return str(headers.get("x-ratelimit-remaining", "")).strip() == "0"

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None, accept: Optional[str] = None,
                 raw: bool = False) -> Any:
        """One request. `raw=True` returns the RESPONSE (not its JSON) — for
        endpoints that serve a binary or a redirect (logs, artifacts)."""
        encoded = self._encode_params(params)
        headers = {"Accept": accept} if accept else None
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        # Retry on READS only: the REST API offers no idempotency
        # key, so replaying a POST would create a second issue, a
        # second comment, a second commit.
        retryable = method.upper() in ("GET", "HEAD")
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                method, url, params=encoded or None, json=json,
                headers=headers, timeout=HTTP_TIMEOUT,
                allow_redirects=not raw)
            if (not self._is_retryable_status(last) or not retryable
                    or attempt == MAX_ATTEMPTS - 1):
                break
            time.sleep(self._retry_after(last, attempt))
        # The `Link` header of the LAST response: it is what says whether a page
        # remains, and `iterate()` rereads it. Set here, the only place that sees
        # the response — otherwise every call family would have to bubble it up.
        self._last_link = (getattr(last, "headers", None) or {}).get("Link")
        if raw:
            return last
        raise_for_upstream(last, service="github")
        if not last.content:
            return {}
        try:
            return last.json()
        except ValueError:
            return last.text

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             per_page: Optional[int] = None,
             page: Optional[int] = None) -> Any:
        """Paginated GET: bounds `per_page` then passes `page`."""
        self._check_per_page(per_page)
        merged: Dict[str, Any] = dict(params or {})
        merged.update({"per_page": per_page, "page": page})
        return self._request("GET", path, params=merged)

    # --- pagination ---------------------------------------------------------

    @staticmethod
    def _rows(payload: Any) -> List[Any]:
        """The ROWS of a list response, whatever its envelope.

        A bare array is returned as-is. An object is searched according to `_LIST_KEYS`
        (`items` for search, `workflow_runs`/`jobs`/`artifacts` for
        Actions…). Without this normalization, a loop written for an array
        would iterate over the dict's KEYS — and return strings instead of
        rows, without raising.
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    def iterate(self, method: Any, *args: Any, max_pages: Optional[int] = None,
                **kwargs: Any) -> Iterator[Any]:
        """Unroll a paginated list and return the ROWS, page after page.

        Writes once two rules that every caller would rewrite badly: follow
        the `Link` header (`rel="next"`) rather than blindly incrementing `page`,
        and extract the rows even when the response is an OBJECT
        (search, Actions) rather than an array.

        `method` is a list method of this client ::

            for issue in client.iterate(client.list_issues, "octo", "repo",
                                        state="open"):
                ...

        `max_pages` bounds the unrolling — useful when the caller serves an agent and
        must stay within a response budget.

        ⚠️ **On search, GitHub stops at 1,000 results** and answers 422
        beyond that: this loop therefore stops by itself, but `total_count` may
        have announced far more. It is not a lost page, it is the API's
        limit.

        ⚠️ Do not pass `page`: this loop manages it.
        """
        if "page" in kwargs:
            raise ValueError(
                "`iterate` handles pagination itself — do not pass `page`.")
        pages = 0
        page = 1
        while True:
            payload = method(*args, page=page, **kwargs)
            rows = self._rows(payload)
            for row in rows:
                yield row
            pages += 1
            if max_pages is not None and pages >= max_pages:
                return
            # `Link` is authoritative when present; otherwise we stop on an
            # empty or incomplete page. `_last_link` is set by `_request` via
            # the session — see `_capture_link`.
            if not rows or not self._has_next_page():
                return
            page += 1

    def _has_next_page(self) -> bool:
        """Did the last response announce a next page (`Link` `rel=next`)?

        GitHub omits `Link` when everything fits on one page: its absence thus means
        "that's all", and it is information, not a gap.
        """
        link = getattr(self, "_last_link", None)
        return bool(link and _LINK_NEXT_RE.search(link))


__all__ = [
    "GitHubClient",
    "DEFAULT_BASE_URL", "DEFAULT_ACCEPT", "DEFAULT_API_VERSION",
    "HTTP_TIMEOUT", "MIN_PER_PAGE", "MAX_PER_PAGE", "DEFAULT_PER_PAGE",
    "SEARCH_MAX_RESULTS", "RETRY_STATUSES", "MAX_ATTEMPTS",
    "ISSUE_STATES", "ISSUE_STATE_WRITES", "ISSUE_SORTS", "SORT_DIRECTIONS",
    "PULL_STATES", "PULL_SORTS", "MERGE_METHODS", "REVIEW_EVENTS",
    "REPO_TYPES", "REPO_SORTS", "ORG_REPO_TYPES", "RUN_STATUSES",
    "MEMBERSHIP_ROLES", "MEMBER_FILTERS", "TEAM_ROLES",
    "COLLABORATOR_PERMISSIONS",
    "SEARCH_CODE_SORTS", "SEARCH_REPO_SORTS", "SEARCH_ISSUE_SORTS",
]
