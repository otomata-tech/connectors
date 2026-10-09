"""Constants of the Luma connector — transport, pagination, enumerations.

Single home: `client.py` re-exports them through its `__all__`, and the
`_api/` mixins import them from here. The backend pins oto-core by tag and
only imports `oto.tools.luma.client`.

Enumerations are named BY USE, never by parameter name: `status` is
`approved|pending` on a calendar's event list, `approved|declined|
pending_approval|waitlist` on a guest status update, and `active|paused` on a
webhook. One constant per parameter name would accept values the API refuses.
"""
from __future__ import annotations

BASE_URL = "https://public-api.luma.com"

# (connect, read) — never an unbounded wait.
HTTP_TIMEOUT = (10, 60)

# A 429 blocks the key for one minute (calendar keys: 200 requests/minute per
# calendar; organization keys: 500/minute per organization) and carries
# `Retry-After`. A READ is retried once when the wait is short; a longer wait
# is the caller's decision, not a stall hidden inside a request. 5xx on a read
# are retried with a short backoff. Writes are never replayed: the API has no
# idempotency key, and a replayed POST could add guests or email them twice.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
RATE_LIMIT_MAX_WAIT = 15.0

# Cursor pagination everywhere: `pagination_cursor` / `pagination_limit` in,
# `{entries, has_more, next_cursor}` out. The server enforces its own maximum.
MIN_LIMIT = 1

SORT_DIRECTIONS = ("asc", "desc", "asc nulls last", "desc nulls last")

# --- events -----------------------------------------------------------------

EVENT_PLATFORMS = ("luma", "external")
EVENT_ACCESS = ("manage", "view")
CALENDAR_EVENT_STATUSES = ("approved", "pending")
CALENDAR_EVENT_SORT_COLUMNS = ("start_at",)
SUBMISSION_MODES = ("auto", "pending")

# --- guests -----------------------------------------------------------------

GUEST_LIST_STATUSES = ("approved", "session", "pending_approval", "invited",
                       "declined", "waitlist")
GUEST_SORT_COLUMNS = ("name", "email", "created_at", "registered_at",
                      "checked_in_at")
GUEST_SET_STATUSES = ("approved", "declined", "pending_approval", "waitlist")
GUEST_ADD_STATUSES = ("approved", "pending_approval", "waitlist")
HOST_ACCESS_LEVELS = ("none", "check-in", "manager")

# --- blasts -----------------------------------------------------------------

BLAST_RECIPIENT_STATUSES = ("approved", "checked_in", "pending_approval",
                            "waitlist", "invited")

# --- tickets ----------------------------------------------------------------

TICKET_PRICE_TYPES = ("free", "paid")
# `percent` carries `percent_off`; `amount` carries `cents_off` and `currency`.
COUPON_DISCOUNT_TYPES = ("percent", "amount")

# --- calendar ---------------------------------------------------------------

CONTACT_SORT_COLUMNS = ("created_at", "event_checked_in_count",
                        "event_approved_count", "name", "revenue_usd_cents")
MEMBERSHIP_LIST_STATUSES = ("approved", "pending", "approved-pending-payment",
                            "declined")
MEMBERSHIP_SET_STATUSES = ("approved", "declined")
TAG_COLORS = ("cranberry", "barney", "red", "green", "blue", "purple",
              "yellow", "orange")
CALENDAR_LAUNCH_STATUSES = ("launched", "coming-soon", "archived")
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png")

# --- webhooks ---------------------------------------------------------------

WEBHOOK_EVENT_TYPES = (
    "*", "calendar.event.added", "calendar.event.submitted",
    "calendar.person.subscribed", "calendar.person.unsubscribed",
    "event.canceled", "event.created", "event.updated", "guest.registered",
    "guest.refunded", "guest.updated", "ticket.registered",
)
WEBHOOK_STATUSES = ("active", "paused")
