"""Affinity — local validation and wire shapes shared by `AffinityClient`:
IDs, enums, typed field values (`field_value`), note HTML (`note_html`), and the
v2 cursor (`next_cursor`). Pure functions, no network. Contract: `client.py`.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

MAX_V2_LIMIT = 100
MAX_FIELD_UPDATES = 100  # PATCH …/fields: up to 100 field values per request

ENTITY_KINDS = ("person", "company", "opportunity")
FIELD_TYPES = ("enriched", "global", "list", "relationship-intelligence")

# v1 interaction types and directions (integers on the wire).
INTERACTION_TYPES = {"meeting": 0, "call": 1, "chat": 2, "email": 3}
INTERACTION_DIRECTIONS = {"sent": 0, "received": 1}

# `valueType` of a field (v2 FieldMetadata) → how a write is shaped. The
# write `type` is the valueType itself; only these are writable.
_SCALAR = {"text", "filterable-text", "number", "datetime"}
_SCALAR_MULTI = {"filterable-text-multi", "number-multi"}
_OPTION = {"dropdown", "ranked-dropdown"}
_OPTION_MULTI = {"dropdown-multi"}
_REF = {"person", "company"}
_REF_MULTI = {"person-multi", "company-multi"}
_LOCATION = {"location"}
_LOCATION_MULTI = {"location-multi"}
WRITABLE_VALUE_TYPES = frozenset(
    _SCALAR | _SCALAR_MULTI | _OPTION | _OPTION_MULTI | _REF | _REF_MULTI
    | _LOCATION | _LOCATION_MULTI)
# Computed by Affinity, never written: formula-number, interaction, note,
# list-multi, reminder.
_LOCATION_KEYS = ("streetAddress", "city", "state", "country", "continent")

# Note bodies: v2 accepts HTML from this tag allowlist only, and fails the
# whole request on anything else.
NOTE_ALLOWED_TAGS = frozenset(
    {"p", "br", "strong", "em", "u", "ol", "ul", "li", "span", "a"})
_TAG = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- local validation ---------------------------------------------------------

def _id(value: Any, name: str) -> int:
    """A positive integer ID, refused locally when it is not one."""
    if isinstance(value, bool):
        raise ValueError(f"`{name}` must be a numeric ID; got {value!r}.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"`{name}` must be a numeric ID; got {value!r}.") from None
    if number < 1:
        raise ValueError(f"`{name}` must be a positive ID; got {value!r}.")
    return number


def _ids(values: Optional[Iterable[Any]], name: str) -> Optional[List[int]]:
    if values is None:
        return None
    if isinstance(values, (str, int)):
        values = [values]
    return [_id(v, name) for v in values]


def _field_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("`field_id` is required (e.g. `field-1234`).")
    return text


def _choice(name: str, value: Optional[str], allowed: Iterable[str]) -> Optional[str]:
    if value is None:
        return None
    allowed = tuple(allowed)
    if value not in allowed:
        raise ValueError(f"`{name}` invalid: {value!r}. Accepted values: "
                         + ", ".join(repr(a) for a in allowed))
    return value


def _limit(limit: Optional[int]) -> Optional[int]:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_V2_LIMIT:
        raise ValueError(f"`limit` must be between 1 and {MAX_V2_LIMIT}; got {limit!r}.")
    return limit


def _iso(value: Any, name: str) -> Optional[str]:
    """An ISO 8601 date-time; a bare date is taken as midnight UTC."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if _DATE.match(text):
        return f"{text}T00:00:00Z"
    if "T" not in text:
        raise ValueError(f"`{name}` must be an ISO 8601 date or date-time; got {value!r}.")
    return text


def field_value(value_type: str, value: Any) -> Dict[str, Any]:
    """The v2 write shape `{"type", "data"}` of a field of `value_type`.

    `value=None` clears the field. Plain Python values are accepted:
    - text / filterable-text: a string; number: an int or float; datetime: an
      ISO 8601 date or date-time;
    - dropdown / ranked-dropdown: an option ID (int) or `{"dropdownOptionId"}`;
      dropdown-multi: a list of those;
    - person / company: an entity ID or `{"id"}`; the `-multi` forms: a list;
    - location: a dict with any of streetAddress, city, state, country,
      continent (missing keys are sent as null); location-multi: a list.
    Text is never matched against dropdown options here: resolve the option
    ID first (`dropdown_options`).
    """
    if value_type not in WRITABLE_VALUE_TYPES:
        raise ValueError(
            f"Fields of type {value_type!r} are computed by Affinity and cannot be "
            "written. Writable types: " + ", ".join(sorted(WRITABLE_VALUE_TYPES)))
    if value is None:
        return {"type": value_type, "data": None}
    multi = value_type.endswith("-multi")
    if multi:
        items = value if isinstance(value, (list, tuple)) else [value]
        if len(items) > 100:
            raise ValueError(f"At most 100 values per field; got {len(items)}.")
    if value_type in _SCALAR:
        data: Any = value
        if value_type == "number":
            data = _number(value)
        elif value_type == "datetime":
            data = _iso(value, "value")
        else:
            data = str(value)
    elif value_type in _SCALAR_MULTI:
        data = ([_number(v) for v in items] if value_type == "number-multi"
                else [str(v) for v in items])
    elif value_type in _OPTION:
        data = {"dropdownOptionId": _option_id(value)}
    elif value_type in _OPTION_MULTI:
        data = [{"dropdownOptionId": _option_id(v)} for v in items]
    elif value_type in _REF:
        data = {"id": _ref_id(value)}
    elif value_type in _REF_MULTI:
        data = [{"id": _ref_id(v)} for v in items]
    elif value_type in _LOCATION:
        data = _location(value)
    else:  # location-multi
        data = [_location(v) for v in items]
    return {"type": value_type, "data": data}


def _number(value: Any) -> float | int:
    if isinstance(value, bool):
        raise ValueError(f"A number field cannot take a boolean ({value!r}).")
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError(f"Not a number: {value!r}.") from None


def _option_id(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("dropdownOptionId", value.get("id"))
    return _id(value, "dropdownOptionId")


def _ref_id(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("id")
    return _id(value, "id")


def _location(value: Any) -> Dict[str, Optional[str]]:
    if not isinstance(value, dict):
        raise ValueError(
            "A location is a dict with streetAddress, city, state, country, "
            f"continent; got {value!r}.")
    unknown = set(value) - set(_LOCATION_KEYS)
    if unknown:
        raise ValueError(f"Unknown location keys: {sorted(unknown)}.")
    return {k: value.get(k) for k in _LOCATION_KEYS}


def note_html(content: str) -> str:
    """Note body as the HTML v2 accepts.

    Plain text (no tag) is escaped and wrapped: blank lines separate `<p>`
    paragraphs, single newlines become `<br>`. Text that already carries tags
    is checked against `NOTE_ALLOWED_TAGS` (no attributes except `href` on
    `<a>`, `http`/`https`/`mailto` only) and refused locally otherwise — the API
    would fail the whole request.
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("A note needs content.")
    if not _TAG.search(text):
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        return "".join(
            "<p>" + "<br>".join(html.escape(line) for line in p.strip().split("\n"))
            + "</p>" for p in paragraphs)
    for match in _TAG.finditer(text):
        tag, attrs = match.group(1).lower(), match.group(2).strip().rstrip("/").strip()
        if tag not in NOTE_ALLOWED_TAGS:
            raise ValueError(
                f"Tag <{tag}> is not accepted in an Affinity note. Allowed: "
                + ", ".join(sorted(NOTE_ALLOWED_TAGS)))
        if attrs and not match.group(0).startswith("</"):
            if tag != "a" or not re.fullmatch(
                    r"""href\s*=\s*(["'])(https?|mailto):[^"']*\1""", attrs):
                raise ValueError(
                    f"Attributes are not accepted on <{tag}> in an Affinity note "
                    "(only href on <a>, with http, https or mailto).")
    return text


def next_cursor(page: Any) -> Optional[str]:
    """The cursor of the next v2 page, read from `pagination.nextUrl`."""
    url = ((page or {}).get("pagination") or {}).get("nextUrl") if isinstance(page, dict) else None
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("cursor")
    return values[0] if values else None


def _note_id(value: Any) -> int:
    note = _id(value, "note_id")
    if note > 2147483647:
        raise ValueError(f"`note_id` out of range: {value!r}.")
    return note


def _note_refs(person_ids, company_ids, opportunity_ids) -> Dict[str, Any]:
    def refs(values, name):
        ids = _ids(values, name)
        return None if ids is None else [{"id": i} for i in ids]
    return {"persons": refs(person_ids, "person_ids"),
            "companies": refs(company_ids, "company_ids"),
            "opportunities": refs(opportunity_ids, "opportunity_ids")}
