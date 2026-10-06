"""Lightfield API client — agent-native CRM (accounts, contacts, opportunities).

API v1 (`https://api.lightfield.app/v1`, docs https://docs.lightfield.app), auth
**Bearer** `sk_lf_…` + **mandatory** version header `Lightfield-Version`. One
method = one endpoint; bodies and responses pass through as-is, the client invents
no semantics. Paths, verbs and scopes were taken page by page from the vendor
reference on 2026-08-18.

⚠️ **The API is in BETA** (stated on the quickstart): "methods, parameters, and
response schemas may change". Hence two design choices: bodies are not re-typed
(the caller composes the dict, the vendor docs are authoritative) and responses come
back raw — a field added upstream reaches the caller instead of being filtered here.

Conventions to know (they condition the caller):

- **Field model PER WORKSPACE.** A record carries
  `{id, createdAt, fields: {slug: {value, valueType}}, relationships: {...}, httpLink}`.
  Slugs are NOT universal: they are discovered at the `…/definitions` endpoints.
  An unknown slug → 400 `code: "unknown_field"` (same for `unknown_relationship`). Never
  hard-code a slug: one customer's workspace does not have another's.

- **`limit`/`offset` pagination, and `limit` CAPS AT 25** (minimum 1). That is low:
  any real collection loops. `_check_limit` refuses out-of-bounds values locally rather
  than letting a 400 go out.

- ⚠️ **Lists read a SEARCH INDEX that may lag behind.** Docs, word
  for word: "Information fetched from list methods is served out of a search index
  that may not have recent changes. If getting the latest version of a record is a
  requirement, you will need to use the individual Retrieve methods." So `list_*`
  and `get_*` are NOT interchangeable: after a write, re-read through `get_*`.

- **Errors**: `{type, code?, param?}` — `code` on some 400/422
  (`unknown_field`, `unknown_relationship`, `relationship_write_limit_exceeded`…),
  surfaced as-is in `UpstreamHTTPError.body`. 429 carries `Retry-After`.

- **Idempotency**: `Idempotency-Key` header (≤255 chars) on POSTs. Key valid for
  24 h, scoped to the organization AND the operation type; a replay returns the cached
  response. ⚠️ If the original call FAILED, replaying the key retries the operation
  instead of re-serving the error — a key therefore only "freezes" successes.

- ⚠️ **`scopes: []` on `/auth/validate` means FULL ACCESS, not "no rights"**
  (docs: "Empty when the key has full access"). The inversion is a security trap
  in reverse: read naively, an all-powerful key would pass for a dead key, and
  we would refuse the connector to the customer who configured their key best. That is why
  reading goes through `scope_granted()` below, never through a hand-written `in`.

Writes: `POST` everywhere, including updates (`POST /v1/accounts/{id}` —
there is neither PUT nor PATCH). This client is NOT read-only.

**Sending email**: allowed (maintainer decision of 2026-08-19), under the same two
locks as the Origami write — the connector only exists if an organization sets
ITS key, and the send goes out from a mailbox that the owner of that key has
connected themselves. The vendor docs say so: "The `from` value must be a bare email address
for a connected mail account owned by the API key user." Without a connected mailbox,
upstream refuses and nothing goes out. ⚠️ Replies and forwards are NOT supported by
the API ("replies and forwards are not supported yet"): `send_email` always creates
a NEW message, even if the caller thinks it is replying to a thread.

**Deliberately absent** (out of scope of the oto connector, not to be "completed" without
a decision): all deletions, merges, file upload sessions,
messages and channels, workflow run state, field history, and the
CRUD of custom object types. ⚠️ Direct consequence: the attachments
of a send are identifiers coming from the upload cycle, which is not exposed —
`attachments` therefore goes through, but there is currently no way to produce the id.

Requires: requests
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# (connect, read) — no unbounded wait.
_HTTP_TIMEOUT = (10, 60)

# API version sent on EVERY request. Pinned here, never copied to a call
# site: a beta API will move, and there must be ONE place to change.
DEFAULT_API_VERSION = "2026-03-01"

# Pagination bounds imposed by the API (docs "List methods").
MIN_LIMIT, MAX_LIMIT = 1, 25

# Statuses we retry: rate limit and transient unavailability. A validation 4xx is
# never retried (it would be rejected identically).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def scope_granted(validate_response: Dict[str, Any], scope: str) -> bool:
    """Does the key described by `/auth/validate` carry `scope`?

    ⚠️ **An EMPTY `scopes` list means full access**, not "no rights" (vendor
    docs: "Empty when the key has full access"). This function exists so that
    this inversion is written ONCE: a naive `if scope in resp["scopes"]` would
    declare the most powerful key unusable.
    """
    if not isinstance(validate_response, dict):
        return False
    scopes = validate_response.get("scopes")
    if not scopes:                      # [] ou absent = full access
        return True
    return scope in scopes


class LightfieldClient:
    """Lightfield v1 client (https://api.lightfield.app), Bearer auth `sk_lf_…`."""

    BASE_URL = "https://api.lightfield.app"

    def __init__(self, api_key: Optional[str] = None,
                 api_version: Optional[str] = None):
        """
        Args:
            api_key: Lightfield key.
            api_version: value of the `Lightfield-Version` header
                (default `DEFAULT_API_VERSION`).
        """
        self.api_key = require(api_key, "LIGHTFIELD_API_KEY")
        self.api_version = api_version or DEFAULT_API_VERSION
        self.session = requests.Session()
        # Key in the HEADER only (never in the query string: it would end up in the URL,
        # hence in the message of any exception, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Lightfield-Version": self.api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    @staticmethod
    def _check_limit(limit: Optional[int]) -> None:
        """A `limit` outside [1, 25] is refused HERE. The API would return a 400; saying so
        locally names the real bound, which nobody guesses."""
        if limit is None:
            return
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("`limit` must be an integer.")
        if not (MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(
                f"`limit` must be between {MIN_LIMIT} and {MAX_LIMIT} "
                f"(Lightfield API cap); got {limit}. "
                "Beyond that, paginate with `offset`.")

    @staticmethod
    def _retry_after(resp: Any, attempt: int) -> float:
        """Delay before retrying: `Retry-After` if present (upstream knows better
        than we do), otherwise exponential backoff."""
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
        if raw:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                pass
        return float(2 ** attempt)

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None,
                 idempotency_key: Optional[str] = None) -> Any:
        # None params removed; booleans as `true`/`false` (requests would write
        # `True`, which the server does not read as a boolean).
        clean: Dict[str, Any] = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            clean[k] = ("true" if v else "false") if isinstance(v, bool) else v

        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None

        # Retries 429/5xx. A POST is retried ONLY if it carries an idempotency key:
        # without one, a response lost in flight would create the record twice.
        retryable = method.upper() == "GET" or idempotency_key is not None
        last = None
        for attempt in range(_MAX_ATTEMPTS):
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=clean or None, json=json,
                headers=headers, timeout=_HTTP_TIMEOUT)
            if (last.status_code not in _RETRY_STATUSES
                    or not retryable or attempt == _MAX_ATTEMPTS - 1):
                break
            time.sleep(self._retry_after(last, attempt))
        raise_for_upstream(last, service="lightfield")
        return last.json() if last.content else {}

    def _write(self, path: str, payload: Dict[str, Any],
               idempotency_key: Optional[str] = None) -> Any:
        """Write POST. If no key is supplied, we generate one: it makes the internal
        retry loop SAFE (the replay will re-serve the response of the first
        attempt). To deduplicate between two distinct calls — an agent replaying
        its turn —, the caller must pass ITS key, stable from one call to the next."""
        if not isinstance(payload, dict):
            raise ValueError("the body must be a dict.")
        key = idempotency_key or f"oto-{uuid.uuid4()}"
        if len(key) > 255:
            raise ValueError("`idempotency_key`: 255 characters maximum.")
        return self._request("POST", path, json=dict(payload), idempotency_key=key)

    def _list(self, path: str, limit: Optional[int], offset: Optional[int],
              extra: Optional[Dict[str, Any]] = None) -> Any:
        self._check_limit(limit)
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(extra or {})
        return self._request("GET", path, params=params)

    # --- auth ---------------------------------------------------------------

    def validate(self) -> Dict[str, Any]:
        """GET /v1/auth/validate — metadata of the current key. NO scope required,
        so this is the authentication probe: it answers even to a very
        restricted key.

        Returns `{active, scopes, subjectType: "user"|"workspace", tokenType: "api_key"}`.
        ⚠️ Read `scopes` with `scope_granted()`: an EMPTY list = full access.
        """
        return self._request("GET", "/v1/auth/validate")

    # --- accounts -----------------------------------------------------------

    def list_accounts(self, limit: Optional[int] = None, offset: Optional[int] = None,
                      **filters: Any) -> Dict[str, Any]:
        """GET /v1/accounts — `{data: [account…]}`. Scope `accounts:read`.

        ⚠️ Serves the search index, potentially lagging: for the up-to-date state
        of a specific record, `get_account`. `filters` = filters by field or
        relationship (key = definition slug), see docs "List methods"."""
        return self._list("/v1/accounts", limit, offset, filters)

    def get_account(self, account_id: str) -> Dict[str, Any]:
        """GET /v1/accounts/{id} — DIRECT read (not the index): the up-to-date version.
        Scope `accounts:read`."""
        return self._request("GET", f"/v1/accounts/{account_id}")

    def create_account(self, payload: Dict[str, Any],
                       idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/accounts — `payload` = `{fields: {slug: value…}, relationships?}`,
        slugs coming from `account_definitions()`. Scope `accounts:create`."""
        return self._write("/v1/accounts", payload, idempotency_key)

    def update_account(self, account_id: str, payload: Dict[str, Any],
                       idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/accounts/{id} — update (the API has neither PUT nor PATCH).
        Scope `accounts:update`."""
        return self._write(f"/v1/accounts/{account_id}", payload, idempotency_key)

    def account_definitions(self) -> Dict[str, Any]:
        """GET /v1/accounts/definitions — fields and relationships of the workspace: THE source
        of slugs. Scope `accounts:read`."""
        return self._request("GET", "/v1/accounts/definitions")

    # --- contacts -----------------------------------------------------------

    def list_contacts(self, limit: Optional[int] = None, offset: Optional[int] = None,
                      **filters: Any) -> Dict[str, Any]:
        """GET /v1/contacts — search index. Scope `contacts:read`."""
        return self._list("/v1/contacts", limit, offset, filters)

    def get_contact(self, contact_id: str) -> Dict[str, Any]:
        """GET /v1/contacts/{id} — direct read. Scope `contacts:read`."""
        return self._request("GET", f"/v1/contacts/{contact_id}")

    def create_contact(self, payload: Dict[str, Any],
                       idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/contacts. Scope `contacts:create`."""
        return self._write("/v1/contacts", payload, idempotency_key)

    def update_contact(self, contact_id: str, payload: Dict[str, Any],
                       idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/contacts/{id}. Scope `contacts:update`."""
        return self._write(f"/v1/contacts/{contact_id}", payload, idempotency_key)

    def contact_definitions(self) -> Dict[str, Any]:
        """GET /v1/contacts/definitions. Scope `contacts:read`."""
        return self._request("GET", "/v1/contacts/definitions")

    # --- opportunities ------------------------------------------------------

    def list_opportunities(self, limit: Optional[int] = None,
                           offset: Optional[int] = None,
                           **filters: Any) -> Dict[str, Any]:
        """GET /v1/opportunities — search index. Scope `opportunities:read`."""
        return self._list("/v1/opportunities", limit, offset, filters)

    def get_opportunity(self, opportunity_id: str) -> Dict[str, Any]:
        """GET /v1/opportunities/{id} — direct read. Scope `opportunities:read`."""
        return self._request("GET", f"/v1/opportunities/{opportunity_id}")

    def create_opportunity(self, payload: Dict[str, Any],
                           idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/opportunities. Scope `opportunities:create`."""
        return self._write("/v1/opportunities", payload, idempotency_key)

    def update_opportunity(self, opportunity_id: str, payload: Dict[str, Any],
                           idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/opportunities/{id}. Scope `opportunities:update`."""
        return self._write(f"/v1/opportunities/{opportunity_id}", payload,
                           idempotency_key)

    def opportunity_definitions(self) -> Dict[str, Any]:
        """GET /v1/opportunities/definitions. Scope `opportunities:read`."""
        return self._request("GET", "/v1/opportunities/definitions")

    # --- notes & tasks ------------------------------------------------------

    def create_note(self, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/notes — note attached to an account/contact/opportunity through a
        relationship. Scope `notes:create`."""
        return self._write("/v1/notes", payload, idempotency_key)

    def note_definitions(self) -> Dict[str, Any]:
        """GET /v1/notes/definitions. Scope `notes:read`."""
        return self._request("GET", "/v1/notes/definitions")

    def create_task(self, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/tasks. Scope `tasks:create`."""
        return self._write("/v1/tasks", payload, idempotency_key)

    def update_task(self, task_id: str, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/tasks/{id}. Scope `tasks:update`."""
        return self._write(f"/v1/tasks/{task_id}", payload, idempotency_key)

    def task_definitions(self) -> Dict[str, Any]:
        """GET /v1/tasks/definitions. Scope `tasks:read`."""
        return self._request("GET", "/v1/tasks/definitions")

    # --- lists --------------------------------------------------------------

    def list_lists(self, limit: Optional[int] = None, offset: Optional[int] = None,
                   **filters: Any) -> Dict[str, Any]:
        """GET /v1/lists. Scope `lists:read`."""
        return self._list("/v1/lists", limit, offset, filters)

    def get_list(self, list_id: str) -> Dict[str, Any]:
        """GET /v1/lists/{id}. Scope `lists:read`."""
        return self._request("GET", f"/v1/lists/{list_id}")

    def create_list(self, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/lists. Scope `lists:create`."""
        return self._write("/v1/lists", payload, idempotency_key)

    def update_list(self, list_id: str, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/lists/{id}. Scope `lists:update`."""
        return self._write(f"/v1/lists/{list_id}", payload, idempotency_key)

    def list_accounts_of_list(self, list_id: str, limit: Optional[int] = None,
                              offset: Optional[int] = None) -> Dict[str, Any]:
        """GET /v1/lists/{listId}/accounts. Scopes `lists:read` AND `accounts:read` —
        a key that only has `lists:read` fails here, not on `get_list`."""
        return self._list(f"/v1/lists/{list_id}/accounts", limit, offset)

    def list_contacts_of_list(self, list_id: str, limit: Optional[int] = None,
                              offset: Optional[int] = None) -> Dict[str, Any]:
        """GET /v1/lists/{listId}/contacts. Scopes `lists:read` AND `contacts:read`."""
        return self._list(f"/v1/lists/{list_id}/contacts", limit, offset)

    def list_opportunities_of_list(self, list_id: str, limit: Optional[int] = None,
                                   offset: Optional[int] = None) -> Dict[str, Any]:
        """GET /v1/lists/{listId}/opportunities. Scopes `lists:read` AND
        `opportunities:read`."""
        return self._list(f"/v1/lists/{list_id}/opportunities", limit, offset)

    # --- meetings & emails (read-only) --------------------------------------

    def list_meetings(self, limit: Optional[int] = None, offset: Optional[int] = None,
                      **filters: Any) -> Dict[str, Any]:
        """GET /v1/meetings. Scope `meetings:read`."""
        return self._list("/v1/meetings", limit, offset, filters)

    def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        """GET /v1/meetings/{id}. Scope `meetings:read`."""
        return self._request("GET", f"/v1/meetings/{meeting_id}")

    def meeting_definitions(self) -> Dict[str, Any]:
        """GET /v1/meetings/definitions. Scope `meetings:read`."""
        return self._request("GET", "/v1/meetings/definitions")

    def list_emails(self, limit: Optional[int] = None, offset: Optional[int] = None,
                    **filters: Any) -> Dict[str, Any]:
        """GET /v1/emails — READ only; sending (`POST /v1/emails/send`) is
        deliberately not exposed here. Scope `emails:read`."""
        return self._list("/v1/emails", limit, offset, filters)

    def get_email(self, email_id: str) -> Dict[str, Any]:
        """GET /v1/emails/{id}. Scope `emails:read`."""
        return self._request("GET", f"/v1/emails/{email_id}")

    @staticmethod
    def _check_from(payload: Dict[str, Any]) -> None:
        """Missing `from` = upstream 400. Saying so HERE names the real constraint: it is not
        just any address, it is a mailbox CONNECTED by the owner of the
        key — the second lock that makes sending acceptable."""
        if not isinstance(payload, dict):
            raise ValueError("the body must be a dict.")
        if not str(payload.get("from") or "").strip():
            raise ValueError(
                "`from` required: the bare address of a CONNECTED mailbox (Google or "
                "Microsoft) belonging to the API key owner. Without a mailbox "
                "connected on the Lightfield side, the send is refused upstream.")

    def send_email(self, payload: Dict[str, Any],
                   idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/emails/send — REAL SEND from the connected mailbox of `from`.
        Scope `emails:create`.

        `payload` = `{from, to, cc?, bcc?, subject?, messageBody?, attachments?}`.
        ⚠️ Always creates a NEW message: the API cannot reply to a thread nor
        forward. ⚠️ `attachments` expects file ids coming from the upload
        cycle, which this client does not expose.

        This is the only action of this client that LEAVES the platform and reaches a
        real person: the calling layer must keep it behind a dry-run.
        """
        self._check_from(payload)
        return self._write("/v1/emails/send", payload, idempotency_key)

    def draft_email(self, payload: Dict[str, Any],
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """POST /v1/emails/draft — draft in the connected mailbox of `from`, NOTHING
        is sent. Scope `emails:create`. Same body as `send_email`.

        Only `from` is required, but a draft carrying ONLY `from` is refused
        (400): at least one `to`/`cc`/`bcc`/`subject`/`messageBody.content`/
        `attachments` is needed. We leave that refusal to upstream — only it knows the exact rule.
        """
        self._check_from(payload)
        return self._write("/v1/emails/draft", payload, idempotency_key)

    # --- object types -------------------------------------------------------

    def list_object_types(self) -> Dict[str, Any]:
        """GET /v1/objects — object types (standard and custom) visible to
        the key: `{data: [{label, objectType}]}`, `objectType` = the slug to pass to
        `object_definitions`."""
        return self._request("GET", "/v1/objects")

    def object_definitions(self, entity_slug: str) -> Dict[str, Any]:
        """GET /v1/objects/{entitySlug}/definitions — fields and relationships of an object
        type, including custom ones."""
        return self._request("GET", f"/v1/objects/{entity_slug}/definitions")
