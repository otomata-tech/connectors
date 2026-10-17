"""Shared shapes of the Amplemarket calls: ids, pagination, unset values."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote, urlsplit


def next_page_after(response: Any) -> Optional[str]:
    """The `page[after]` cursor of the next page, read in `_links.next.href`;
    None on the last page (the link is absent)."""
    links = response.get("_links") if isinstance(response, dict) else None
    href = ((links or {}).get("next") or {}).get("href")
    if not href:
        return None
    values = parse_qs(urlsplit(href).query).get("page[after]")
    return values[0] if values else None


def path_id(name: str, value: Any) -> str:
    """A path segment: a non-empty id, percent-encoded (no `/` smuggled in)."""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{name} is required")
    return quote(text, safe="@")


def clean(values: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the unset entries (None), keep False/0/empty strings as given."""
    return {k: v for k, v in values.items() if v is not None}


def _encode(value: Any) -> Any:
    """A query value: booleans lowercase, lists repeated (`name[]=a&name[]=b`)."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def encode_query(params: Dict[str, Any]) -> Dict[str, Any]:
    """The query string: unset parameters dropped, values encoded."""
    return {k: _encode(v) for k, v in clean(params).items()}


def page_params(page_size: Optional[int], page_after: Optional[str],
                page_before: Optional[str] = None) -> Dict[str, Any]:
    """The cursor pagination parameters, under their API names."""
    return {"page[size]": page_size, "page[after]": page_after,
            "page[before]": page_before}


def items(name: str, values: Any, key: str) -> list:
    """`["a@x.com"]` or `[{"email": "a@x.com"}]` → `[{"email": "a@x.com"}]`."""
    if not values:
        raise ValueError(f"{name} must be a non-empty list")
    return [v if isinstance(v, dict) else {key: v} for v in values]
