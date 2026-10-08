"""Local checks of what a call sends: path ids and JSON:API bodies.

A failed check raises `ValueError` and nothing is sent.
"""

from __future__ import annotations

import re
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9]+$")
MAX_LIST_MEMBERS = 1000


def path_id(name: str, value: Any) -> str:
    """A Klaviyo id placed in a path: letters and digits only."""
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"`{name}` must be a Klaviyo id (letters and digits).")
    return value


def resource(data: Any, kind: str, where: str) -> dict:
    """`{data: <resource>}` for a resource object of the given type."""
    if not isinstance(data, dict) or data.get("type") != kind:
        raise ValueError(f"{where} takes data = {{type: {kind!r}, "
                         "attributes: {...}}.")
    if not isinstance(data.get("attributes"), dict):
        raise ValueError(f"{where}: data.attributes must be an object.")
    return {"data": data}


def profile_refs(data: Any, where: str) -> dict:
    """`{data: [...]}` for 1 to 1000 `{type: "profile", id}` objects."""
    if (not isinstance(data, list) or not 0 < len(data) <= MAX_LIST_MEMBERS
            or any(not isinstance(ref, dict) or ref.get("type") != "profile"
                   or not isinstance(ref.get("id"), str)
                   or not _ID.match(ref["id"]) for ref in data)):
        raise ValueError(f"{where} takes data = 1 to {MAX_LIST_MEMBERS} "
                         "{type: 'profile', id} objects.")
    return {"data": data}


def job_profiles(data: Any, kind: str, limit: int, where: str) -> dict:
    """A consent job: its type, and 1 to `limit` profiles."""
    body = resource(data, kind, where)
    profiles = ((data["attributes"].get("profiles") or {}).get("data"))
    if not isinstance(profiles, list) or not 0 < len(profiles) <= limit:
        raise ValueError(f"{where} takes 1 to {limit} profiles in "
                         "data.attributes.profiles.data.")
    return body
