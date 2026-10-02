"""What the Boond API accepts, as tables — the client refuses locally what the
API would refuse, before spending a call of the monthly quota.

Transcribed from the API reference (https://doc.boondmanager.com/api-externe/):
the creation body schemas (`schemas/<entity>/bodyPost.json`) and the search
query parameters (`resources/<entity>/search.raml`). Copies of those files live
in `tests/fixtures/boondmanager/`, and `tests/test_boondmanager_spec.py` checks
these tables against them field by field, and every body this client builds
against the official schemas.

Boond rejects unknown attributes and relationships (`additionalProperties:
false`): a misspelt field is an error, never silently ignored.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

ENTITIES = ("contacts", "companies", "opportunities", "actions")

# `2026-10-02T09:30:00+0200` — date-times carry their offset, without a colon.
DATETIME = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4}$"
DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

_S, _I, _N, _B, _A, _O = "string", "integer", "number", "boolean", "array", "object"


def _s(max_length: Optional[int] = None, pattern: Optional[str] = None) -> dict:
    out: Dict[str, Any] = {"type": _S}
    if max_length is not None:
        out["maxLength"] = max_length
    if pattern is not None:
        out["pattern"] = pattern
    return out


def _i(minimum: Optional[int] = None) -> dict:
    return {"type": _I} if minimum is None else {"type": _I, "minimum": minimum}


_ORIGIN = {"type": _O, "shape": "origin"}
_SOCIAL = {"type": _A, "items": _O, "shape": "socialNetworks"}
_IDS = {"type": _A, "items": _S}

# Attributes of a new record. `billingDetails` (company) is deliberately left
# out: a nested create-or-link structure this connector does not write.
ATTRIBUTES: Dict[str, Dict[str, dict]] = {
    "contacts": {
        "creationDate": _s(pattern=DATETIME), "civility": _i(0),
        "firstName": _s(100), "lastName": _s(100), "state": _i(0),
        "email1": _s(100), "email2": _s(100), "email3": _s(100),
        "phone1": _s(20), "phone2": _s(20), "fax": _s(20), "address": _s(250),
        "postcode": _s(10), "town": _s(100), "country": _s(100),
        "subDivision": _s(6), "origin": _ORIGIN, "activityAreas": _IDS,
        "informationComments": _s(250), "tools": _IDS, "department": _s(100),
        "function": _s(100), "socialNetworks": _SOCIAL, "typesOf": _IDS,
    },
    "companies": {
        "creationDate": _s(pattern=DATETIME), "name": _s(100), "state": _i(0),
        "website": _s(100), "phone1": _s(20), "fax": _s(20), "address": _s(250),
        "postcode": _s(10), "town": _s(100), "country": _s(100),
        "subDivision": _s(6), "number": _s(250), "origin": _ORIGIN,
        "staff": _i(0), "expertiseArea": _s(100), "informationComments": _s(1000),
        "departments": _IDS, "vatNumber": _s(100), "registrationNumber": _s(100),
        "legalStatus": _s(100), "registeredOffice": _s(100), "apeCode": _s(100),
        "socialNetworks": _SOCIAL,
    },
    "opportunities": {
        "creationDate": _s(pattern=DATETIME), "title": _s(150),
        "reference": _s(250), "state": _i(0),
        "stateReason": {"type": _O, "shape": "stateReason"}, "typeOf": _i(1),
        "origin": _ORIGIN, "expertiseArea": _s(), "activityAreas": _IDS,
        "tools": _IDS, "place": _s(), "duration": _i(), "estimatesExcludingTax":
        {"type": _N}, "turnoverEstimatedExcludingTax": {"type": _N},
        "weighting": {"type": _N}, "isVisible": {"type": _B},
        # A date, or the literal "immediate".
        "startDate": {"type": _S, "shape": "startDate"},
        "endDate": _s(pattern=r"^|[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
        "currency": _i(0), "exchangeRate": {"type": _N}, "currencyAgency": _i(0),
        "exchangeRateAgency": {"type": _N},
    },
    "actions": {
        "startDate": _s(pattern=DATETIME), "endDate": _s(pattern=DATETIME),
        "startTimezone": _s(), "endTimezone": _s(), "typeOf": _i(0),
        "title": _s(8192), "description": _s(1000), "text": _s(65000),
        "location": _s(), "guests": {"type": _A, "items": _S},
        "synchronizeWithAdvancedAppCalendar": {"type": _B},
    },
}

# Relationships of a new record: name → (accepted record types, is a list,
# accepts null).
RELATIONSHIPS: Dict[str, Dict[str, tuple]] = {
    "contacts": {
        "mainManager": (("resource",), False, False),
        "agency": (("agency",), False, False),
        "pole": (("pole",), False, True),
        "influencers": (("resource",), True, False),
        "company": (("company",), False, False),
    },
    "companies": {
        "mainManager": (("resource",), False, False),
        "agency": (("agency",), False, False),
        "pole": (("pole",), False, True),
        "influencers": (("resource",), True, False),
        "parentCompany": (("company",), False, True),
    },
    "opportunities": {
        "mainManager": (("resource",), False, False),
        "agency": (("agency",), False, False),
        "pole": (("pole",), False, True),
        "company": (("company",), False, True),
        "contact": (("contact",), False, True),
    },
    "actions": {
        "mainManager": (("resource",), False, False),
        "company": (("company",), False, False),
        "dependsOn": (("candidate", "contact", "invoice", "opportunity", "order",
                       "project", "resource"), False, False),
    },
}

REQUIRED_ATTRIBUTES = {"contacts": ("firstName", "lastName"),
                       "companies": ("name",), "opportunities": ("title",),
                       "actions": ("typeOf",)}
REQUIRED_RELATIONSHIPS = {"contacts": ("company",), "companies": (),
                          "opportunities": (), "actions": ("dependsOn",)}

_SOCIAL_NETWORKS = ("facebook", "viadeo", "linkedin", "x")

# --- search ----------------------------------------------------------------

MAX_RESULTS = {"contacts": 500, "companies": 500, "opportunities": 500,
               "actions": 100}
KEYWORD_PREFIXES = {
    "contacts": ("CCON", "CSOC"),
    "companies": ("CSOC",),
    "opportunities": ("AO", "PROD", "CAND", "COMP", "CCON", "CSOC"),
    "actions": ("COMP", "CAND", "PRJ", "CCON", "CSOC", "AO", "BDC", "FACT"),
}
KEYWORDS_TYPES = {
    "contacts": ("default", "lastName", "firstName", "fullName", "strictFullName",
                 "companyFullName", "emails", "phones", "socialNetworks"),
    "companies": ("default", "name", "phones", "emails", "socialNetworks"),
    "opportunities": (),
    # `relatedActions` only means something with `returnRelatedActions`, which
    # this client does not send.
    "actions": (),
}
PERIODS = {
    "contacts": ("created", "updated", "noAction", "withActions", "withoutActions"),
    "companies": ("created", "updated", "noAction", "withActions", "withoutActions"),
    "opportunities": ("created", "updatedPositioning", "started", "updated",
                      "closingDate", "noAction", "withActions", "withoutActions"),
    "actions": ("started", "created", "updated"),
}
SORTS = {
    "contacts": ("company.name", "town", "lastName", "firstName", "function",
                 "state", "company.expertiseArea", "mainManager.lastName",
                 "updateDate"),
    "companies": ("name", "information", "town", "state", "expertiseArea",
                  "mainManager.lastName", "updateDate"),
    "opportunities": ("creationDate", "title", "company.name", "place",
                      "numberOfActivePositionings", "startDate", "endDate",
                      "duration", "state", "alertCount", "closingDate",
                      "updateDate", "answerDate",
                      "totalWeightedTurnOverExcludingTax", "mainManager.lastName"),
    "actions": ("startDate", "typeOf", "mainManager.lastName", "dependsOn.email1",
                "dependsOn.lastName", "dependsOn.reference", "dependsOn.name",
                "dependsOn.title", "dependsOn.id", "dependsOn.number"),
}
# List filters forwarded as `name[]=value`. Anything else (CSV export, download
# center, encoding, app entity fields…) is refused rather than forwarded.
SEARCH_FILTERS = {
    "contacts": ("states", "companyStates", "typesOf", "activityAreas", "tools",
                 "expertiseAreas", "origins", "flags", "influencers",
                 "returnMoreData"),
    "companies": ("states", "expertiseAreas", "origins", "flags", "influencers",
                  "returnMoreData"),
    "opportunities": ("opportunityStates", "opportunityTypes", "positioningStates",
                      "expertiseAreas", "activityAreas", "tools", "places",
                      "durations", "origins", "flags", "returnMoreData"),
    "actions": ("actionTypes", "origins", "flags"),
}
RETURN_MORE_DATA = {
    "contacts": ("lastAction", "previousAction", "nextAction"),
    "companies": ("previousAction", "nextAction"),
    "opportunities": ("hrManager", "previousAction", "nextAction", "alerts"),
    "actions": (),
}


# --- validation --------------------------------------------------------------

_JSON_TYPES = {_S: (str,), _I: (int,), _N: (int, float), _B: (bool,),
               _A: (list,), _O: (dict,)}


def _is(value: Any, json_type: str) -> bool:
    if isinstance(value, bool) and json_type != _B:
        return False
    return isinstance(value, _JSON_TYPES[json_type])


def _shape(name: str, shape: str, value: Any) -> None:
    if shape == "origin":
        if set(value) != {"typeOf", "detail"} or not _is(value["typeOf"], _I) \
                or value["typeOf"] < -1 or not _is(value["detail"], _S) \
                or len(value["detail"]) > 100:
            raise ValueError(f"`{name}` must be {{\"typeOf\": <origin id>, "
                             "\"detail\": <text, 100 max>}} (both keys).")
    elif shape == "stateReason":
        if not set(value) <= {"typeOf", "detail"} \
                or ("typeOf" in value and (not _is(value["typeOf"], _I)
                                           or value["typeOf"] < 0)) \
                or ("detail" in value and not _is(value["detail"], _S)):
            raise ValueError(f"`{name}` must be {{\"typeOf\": <id>, \"detail\": "
                             "<text>}}.")
    elif shape == "socialNetworks":
        if len(value) > 4:
            raise ValueError(f"`{name}`: at most 4 entries.")
        for item in value:
            if not isinstance(item, dict) or set(item) != {"network", "url"} \
                    or item["network"] not in _SOCIAL_NETWORKS \
                    or not _is(item["url"], _S) or len(item["url"]) > 250:
                raise ValueError(
                    f"`{name}` entries must be {{\"network\": "
                    + "|".join(_SOCIAL_NETWORKS) + ", \"url\": <250 max>}}.")
    elif shape == "startDate":
        if value != "immediate" and not re.match(DATE, value):
            raise ValueError(f"`{name}` must be YYYY-MM-DD or \"immediate\"; "
                             f"got {value!r}.")


def check_attributes(entity: str, attributes: Dict[str, Any]) -> None:
    """Refuse what the creation schema refuses: unknown names, wrong JSON
    types, texts over their length, malformed dates, negative ids."""
    spec = ATTRIBUTES[entity]
    for name, value in attributes.items():
        if name not in spec:
            raise ValueError(
                f"`{name}` is not an attribute of a new "
                f"{entity[:-1] if entity != 'companies' else 'company'} in Boond. "
                "Accepted: " + ", ".join(spec) + ".")
        rule = spec[name]
        if not _is(value, rule["type"]):
            raise ValueError(f"`{name}` must be a JSON {rule['type']}; "
                             f"got {value!r}.")
        if rule["type"] == _S:
            if "maxLength" in rule and len(value) > rule["maxLength"]:
                raise ValueError(f"`{name}`: {rule['maxLength']} characters "
                                 f"max; got {len(value)}.")
            if "pattern" in rule and not re.search(rule["pattern"], value):
                hint = (" — e.g. 2026-10-02T09:30:00+0200"
                        if rule["pattern"] == DATETIME else "")
                raise ValueError(f"`{name}` is malformed: {value!r}{hint}.")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"`{name}` must be >= {rule['minimum']}; got {value}.")
        if rule["type"] == _A and "items" in rule:
            if any(not _is(v, rule["items"]) for v in value):
                raise ValueError(f"`{name}` items must be JSON {rule['items']}s.")
        if "shape" in rule:
            _shape(name, rule["shape"], value)


def check_relationships(entity: str, relationships: Dict[str, Any]) -> None:
    """Refuse unknown relationship names and record types the schema does not
    accept for them (`dependsOn` of an action: contact, opportunity…)."""
    spec = RELATIONSHIPS[entity]
    for name, value in relationships.items():
        if name not in spec:
            raise ValueError(f"`{name}` is not a relationship of a new record of "
                             f"{entity}. Accepted: " + ", ".join(spec) + ".")
        types, many, nullable = spec[name]
        if value is None:
            if not nullable:
                raise ValueError(f"relationship `{name}` cannot be null.")
            continue
        items = value if isinstance(value, list) else [value]
        if many != isinstance(value, list):
            raise ValueError(f"relationship `{name}` must be "
                             + ("a list of " if many else "a single ")
                             + '{"type": …, "id": …}.')
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in types:
                raise ValueError(f"relationship `{name}` accepts type "
                                 + " | ".join(types) + f"; got {item!r}.")
    if entity == "opportunities" and (
            bool(relationships.get("company")) != bool(relationships.get("contact"))):
        raise ValueError("an opportunity's `company` and `contact` go together: "
                         "set both or neither.")
