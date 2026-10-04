"""The one gate an id passes before it enters a Notion API path.

Notion ids are 32 hexadecimal characters, written with or without dashes
(the UUID form). Agents also paste page URLs. Anything else is refused here,
so that a value such as ``"../pages/X"`` or ``"123?x=y"`` never reaches
``https://api.notion.com/v1/<...>`` — a forged path would hit another
endpoint with the integration's token.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_HEX32 = re.compile(r"[0-9a-f]{32}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_URL_TAIL = re.compile(r"([0-9a-f]{32})$")


def notion_id(value, label: str = "id") -> str:
    """Compact lowercase id (32 hex) for `value`, or `ValueError`.

    Accepts a bare id (dashes allowed) or a Notion URL whose last path segment
    ends with the id (``https://www.notion.so/ws/My-Page-<id>?v=...``; the
    query, e.g. a view id, is ignored).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: a Notion id is required.")
    raw = value.strip().lower()
    if raw.startswith(("http://", "https://")):
        segment = urlsplit(raw).path.rstrip("/").rsplit("/", 1)[-1]
        found = _URL_TAIL.search(segment)
        if found:
            return found.group(1)
        if _UUID.fullmatch(segment):
            return segment.replace("-", "")
    else:
        compact = raw.replace("-", "")
        if _HEX32.fullmatch(compact) and (raw == compact or _UUID.fullmatch(raw)):
            return compact
    raise ValueError(
        f"{label} {value!r}: not a Notion id — expected 32 hexadecimal "
        f"characters (dashes allowed) or a Notion page URL.")
