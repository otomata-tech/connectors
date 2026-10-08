"""Shaping of Typeform arguments: querystring cleaning, comma-joined lists, ids
placed in a path, and the webhook signing secret kept out of what is returned.

Re-exported by `client.py`, where these helpers lived first.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Union
from urllib.parse import quote

ListParam = Union[str, Iterable[str], None]


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become the literal
    string 'None' in the querystring."""
    return {k: v for k, v in params.items() if v is not None}


def _csv(name: str, value: ListParam) -> Optional[str]:
    """A list parameter as the single comma-separated string Typeform expects.
    A string is taken as already joined; an item of a list containing a comma
    is refused (it would be split in two upstream)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    items = list(value)
    for item in items:
        if not isinstance(item, str) or not item:
            raise ValueError(f"`{name}`: every item must be a non-empty string.")
        if "," in item:
            raise ValueError(f"`{name}`: item {item!r} contains a comma — it would "
                             "be split in two.")
    return ",".join(items) or None


def _segment(name: str, value: str) -> str:
    """An id placed in a path: escaped, and never `.`/`..` (which `quote` leaves
    intact and which would change the path)."""
    if not isinstance(value, str) or not value.strip() or value in (".", ".."):
        raise ValueError(f"`{name}` must be a non-empty identifier.")
    return quote(value, safe="")


def _without_secret(webhook: Any) -> Any:
    """A webhook as returned to the caller: without its HMAC signing `secret`,
    which the caller set and has no reason to read back."""
    if isinstance(webhook, dict):
        return {k: v for k, v in webhook.items() if k != "secret"}
    return webhook
