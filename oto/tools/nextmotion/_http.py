"""Nextmotion — protocol helpers shared by the client and its `_api` mixins.

Kept apart from `client.py` so the mixins can import them without importing the
client that composes them (no circular import).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MAX_LIMIT = 100


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    """Drops `None` values — an omitted kwarg must not become the literal
    string 'None' in the querystring, nor a null in a JSON body."""
    return {k: v for k, v in params.items() if v is not None}


def _query(params: Dict[str, Any]) -> Dict[str, Any]:
    """`_clean`, plus booleans spelled `true`/`false`: `requests` would send
    Python's `True`, which the spec does not promise to accept."""
    return {k: (str(v).lower() if isinstance(v, bool) else v)
            for k, v in _clean(params).items()}


def _id(value: Any, name: str) -> str:
    """A path identifier must be a UUID (the spec types every one of them so).
    Anything else — empty, `../`, a slash — is refused before the URL exists."""
    text = str(value) if value is not None else ""
    if not _UUID.fullmatch(text):
        raise ValueError(f"{name} doit être un UUID — reçu {value!r}.")
    return text


def _opt_id(value: Any, name: str) -> Optional[str]:
    """`_id` for an optional filter: `None` stays omitted."""
    return None if value is None else _id(value, name)


def _ids(values: Optional[Iterable[Any]], name: str) -> Optional[List[str]]:
    """A list filter of UUIDs (sent as a repeated query parameter)."""
    if values is None:
        return None
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} doit être une liste d'UUID — reçu {values!r}.")
    return [_id(v, name) for v in values]


def _page(limit: int, offset: int) -> Dict[str, int]:
    if not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit doit être entre 1 et {_MAX_LIMIT} — reçu {limit}.")
    if offset < 0:
        raise ValueError(f"offset doit être >= 0 — reçu {offset}.")
    return {"limit": limit, "offset": offset}
