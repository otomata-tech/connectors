"""Productlane connector constants — bounds, enums, transport.

Single home: `client.py` re-exports them via its `__all__`, and the `_api/`
mixins import them from here. The backend pins oto-core by tag and only imports
`oto.tools.productlane.client`.

⚠️ **Enums are SCOPED TO THEIR ENDPOINT**, and that is deliberate: the upstream
OpenAPI schema reuses the same parameter names for different sets of
values. `status` is `open|snoozed|done` on a thread and
`draft|open|accepted|rejected|superseded` on a doc draft; `type` is
`EMAIL|DOMAIN` on a blocked sender and `email|slack|chat|live_chat|feedback`
on a message. A "global" constant per parameter name would therefore accept
values that upstream rejects, and reject values it accepts — hence one
name per usage, never per parameter.
"""
from __future__ import annotations

# (connect, read) — no unbounded wait.
HTTP_TIMEOUT = (10, 60)

# CURSOR pagination, uniform across all v2 lists: no `page`, no
# `offset`, no `skip` anywhere. Order fixed server-side (`created_at DESC, id
# DESC`), with no parameter to change it.
DEFAULT_LIMIT = 50
MIN_LIMIT, MAX_LIMIT = 1, 200

# Retried statuses. Upstream limits PER KEY: 1000 GET/minute, 60 writes/minute,
# with a 2x burst over 10 s. The 429 carries `Retry-After`.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

# --- enums, by where they apply ---------------------------------------------

THREAD_STATUSES = ("open", "snoozed", "done")
THREAD_TABS = ("open", "new", "needs-response", "my", "snoozed", "done")
PAIN_LEVELS = ("UNKNOWN", "LOW", "MEDIUM", "HIGH")
THREAD_ORIGINS = (
    "in_app", "portal", "support_portal", "email", "slack", "slack_connect",
    "intercom", "intercom_attachment", "zendesk", "zendesk_attachment",
    "front_attachment", "zapier", "hubspot", "plain", "api", "live_chat",
    "ai_chat", "calendar", "widget", "teams", "linear", "upvote",
)

# `expand` on GET /threads/{id}: comma-separated list. Upstream
# IGNORES an unknown value (it does not reject) — so a typo would end
# in a response without the requested data, silently. We reject
# locally so the discrepancy is visible.
THREAD_EXPANDS = ("messages", "comments")

MESSAGE_ORDERS = ("asc", "desc")
MESSAGE_TYPES = ("email", "slack", "chat", "live_chat", "feedback")
MESSAGE_DIRECTIONS = ("inbound", "outbound")

BLOCKED_SENDER_TYPES = ("EMAIL", "DOMAIN")

PROJECT_STATES = ("backlog", "planned", "started", "completed", "canceled")
ROADMAP_SORTS = ("created_at", "total_score")

DOC_VISIBILITIES = ("public", "agent", "internal", "unlisted")
#: `all` only exists as a list FILTER, never on write — an article cannot
#: "be" of visibility `all`.
DOC_VISIBILITY_FILTERS = DOC_VISIBILITIES + ("all",)
DOC_KINDS = ("doc", "link")
DOC_KIND_FILTERS = DOC_KINDS + ("all",)

DRAFT_KINDS = ("edit", "create", "delete")
DRAFT_STATUSES = ("draft", "open", "accepted", "rejected", "superseded")

#: Linear priorities, numbered the way Linear numbers them. They are NOT
#: increasing urgency levels: `0` = no priority, `1` = the highest.
ISSUE_PRIORITIES = (0, 1, 2, 3, 4)

__all__ = [
    "HTTP_TIMEOUT", "DEFAULT_LIMIT", "MIN_LIMIT", "MAX_LIMIT",
    "RETRY_STATUSES", "MAX_ATTEMPTS",
    "THREAD_STATUSES", "THREAD_TABS", "PAIN_LEVELS", "THREAD_ORIGINS",
    "THREAD_EXPANDS", "MESSAGE_ORDERS", "MESSAGE_TYPES", "MESSAGE_DIRECTIONS",
    "BLOCKED_SENDER_TYPES", "PROJECT_STATES", "ROADMAP_SORTS",
    "DOC_VISIBILITIES", "DOC_VISIBILITY_FILTERS", "DOC_KINDS", "DOC_KIND_FILTERS",
    "DRAFT_KINDS", "DRAFT_STATUSES", "ISSUE_PRIORITIES",
]
