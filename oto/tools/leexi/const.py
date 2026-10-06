"""Constants of the Leexi connector — bounds, enumerations, transport settings.

Single home: `client.py` re-exports them via its `__all__`, and the `_api/`
mixins import them from here. An oto-core tag freezes this import path for the
backend, which only pins `oto.tools.leexi.client`.
"""
from __future__ import annotations

# (connect, read) — no unbounded wait.
HTTP_TIMEOUT = (10, 60)

# Pagination bounds imposed by the API (« Pagination » docs).
MIN_ITEMS, MAX_ITEMS = 1, 100
DEFAULT_ITEMS = 10

# The parameters upstream expects as `name[]=…`, repeated (Rails). Without the
# suffix, only the LAST value is read and the filter lies — see the header of
# `client.py`. All six are written with their brackets in the vendor docs.
ARRAY_PARAMS = frozenset({
    "source_id", "owner_uuid", "participating_user_uuid",
    "customer_phone_number", "customer_email_address", "roles",
})

# Retried statuses: rate limit (50/min, 10/min on call creation) and
# transient outages. A validation 4xx is never retried.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

# Accepted orders and filters, collected from the OpenAPI of each list page.
# Refusing an out-of-list value locally avoids a 400 whose message does not say
# which ones are valid.
CALL_ORDERS = ("created_at desc", "created_at asc", "performed_at desc",
               "performed_at asc", "updated_at desc", "updated_at asc")
CALL_DATE_FILTERS = ("created_at", "performed_at", "updated_at")

MEETING_ORDERS = ("created_at desc", "created_at asc", "start_time desc",
                  "start_time asc", "end_time desc", "end_time asc")
MEETING_DATE_FILTERS = ("start_time", "end_time")
MEETING_ORIGINS = ("calendar", "manual", "api")

__all__ = [
    "HTTP_TIMEOUT", "MIN_ITEMS", "MAX_ITEMS", "DEFAULT_ITEMS",
    "ARRAY_PARAMS", "RETRY_STATUSES", "MAX_ATTEMPTS",
    "CALL_ORDERS", "CALL_DATE_FILTERS",
    "MEETING_ORDERS", "MEETING_DATE_FILTERS", "MEETING_ORIGINS",
]
