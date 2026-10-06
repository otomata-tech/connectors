"""The parameter GUARDS of the PayFit API — shared by the transport and the
call families in `_api/`.

They live in their own module for a mechanical reason: `client.py` composes the
mixins in `_api/`, so a mixin importing `client.py` would close a cycle. They
depend on nothing from the client.

Three refusals, all raised BEFORE the network because the 400 they would avoid
names no field on the PayFit side:

- an identifier that would rewrite the URL (`../absences`, `id?x=1`);
- a month that is not `AAAAMM` — the `2026-01` form, the only one a human
  writes spontaneously, is refused by the API;
- a page size outside 1..50.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

MAX_LIMIT = 50
# Identifiers in the spec are Mongo ObjectIds, UUIDs or digits: a path segment
# is refused unless it is made of those characters only.
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# A month, as every dated endpoint of this API wants it: YYYYMM, January = "01".
_MONTH = re.compile(r"2\d{3}(0[1-9]|1[0-2])")


def clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become 'None' in the URL."""
    return {k: v for k, v in params.items() if v is not None}


def ident(value: Any, name: str) -> str:
    text = str(value) if value is not None else ""
    if not _ID.fullmatch(text):
        raise ValueError(f"{name} is invalid — got {value!r}.")
    return text


def month(value: Any, name: str = "date") -> str:
    """`AAAAMM` (YYYYMM), the only month format this API accepts."""
    text = str(value) if value is not None else ""
    if not _MONTH.fullmatch(text):
        raise ValueError(
            f"{name} must be a month in AAAAMM format (January = '01') — got "
            f"{value!r}.")
    return text


def page(limit: int, cursor: Optional[str]) -> Dict[str, Any]:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT} — got {limit}.")
    return clean({"maxResults": limit, "nextPageToken": cursor or None})
