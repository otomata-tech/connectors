"""
Cognism Search API — allow-lists for closed-set filter fields.

These values are copied verbatim from the Cognism docs (developers.cognism.com,
Search Contacts endpoint) — NOT derived from a dynamic Filter API endpoint
(those — regions/countries/states/industries/sic/isic/naics/technologies/
skills/companySizes — are deliberately absent from here: they are
long and evolve on Cognism's side, so they are consumed live via
`CognismClient.filter_values(kind)`, never frozen in this module).

Goal: turn an enum typo (the most likely and sneakiest failure mode
with a ~150-field DSL — the API answers 200 with an empty page,
not an error) into an explicit `ValueError` BEFORE the network call, rather than
letting through a request that "works" but never matches anything.

⚠️ Nesting trap: the "account"-side fields (types/fundingEvent/
hiringEvent/accountSearchOptions) live under `account.*` in the `search_contacts`
body (the contact is the root, the account is nested), but
at the ROOT (without the `account.` prefix) in the `search_accounts` body (the
account IS the root, there). Same field names, different depth depending on the
endpoint → two path tables (`_CONTACT_ENUM_FIELDS` /
`_ACCOUNT_ENUM_FIELDS`), not a single one, so as not to validate at the wrong level
and let an invalid value through on the `search_accounts` side.
"""
from __future__ import annotations

from typing import Any, Dict

SENIORITY = {"Manager", "Director", "Partner", "CXO", "Owner", "VP"}

JOB_FUNCTIONS = {
    "Oversight", "Technology", "Operations", "Sales", "Marketing",
    "Client Success", "HR", "Accounting", "Business", "Production",
}

MANAGEMENT_LEVELS = {
    "Entry-Level", "Team-Lead", "Experienced Staff", "Executive-Level",
    "Senior Leadership", "Middle-Management", "CxO",
}

ACCOUNT_TYPES = {
    "Public Company", "Educational", "Educational Institution",
    "Government Agency", "Partnership", "Privately Held",
    "Self-Employed", "non profit",
}

FUNDING_TYPES = {
    "venture", "seed", "grant", "private_equity", "angel",
    "debt_financing", "corporate_round", "convertible note",
    "equity_crowfunding",
}

FUNDING_SERIES = {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"}

HIRING_EVENT_DEPARTMENTS = {
    "legal", "it", "administration", "marketing", "sales", "R&D",
    "customer", "operations", "finance",
}

SORT_FIELDS = {
    "LastConfirmedContactDESC", "LastConfirmedContactASC",
    "EmailQualityDESC", "EmailQualityASC",
    "ProfileScoreDESC", "ProfileScoreASC",
}

EXISTS_MISSING = {"exists", "missing"}
LOCATION_TYPE = {"ALL", "HQ"}
AND_OR = {"AND", "OR"}

# "Account" fields — same names, prefixed `account.` in search_contacts,
# at the root in search_accounts. Generated once to avoid
# drift between the two tables.
_ACCOUNT_SIDE_FIELDS: Dict[str, set] = {
    "types": ACCOUNT_TYPES,
    "fundingEvent.fundingType": FUNDING_TYPES,
    "fundingEvent.series": FUNDING_SERIES,
    "hiringEvent.department": HIRING_EVENT_DEPARTMENTS,
    "accountSearchOptions.filter_email": EXISTS_MISSING,
    "accountSearchOptions.filter_domain": EXISTS_MISSING,
    "accountSearchOptions.location_type": LOCATION_TYPE,
    "accountSearchOptions.events_operator": AND_OR,
    "accountSearchOptions.operators.technologies": AND_OR,
    "accountSearchOptions.operators.excludedTechnologies": AND_OR,
}

# search_contacts: contact fields at the root + account fields under `account.`
# + the 3 closed fields duplicated under `previousAccounts.*` (past accounts).
_CONTACT_ENUM_FIELDS: Dict[str, set] = {
    "seniority": SENIORITY,
    "jobFunctions": JOB_FUNCTIONS,
    "managementLevel": MANAGEMENT_LEVELS,
    "searchOptions.sort_fields": SORT_FIELDS,
    "previousAccounts.seniority": SENIORITY,
    "previousAccounts.jobFunction": JOB_FUNCTIONS,
    "previousAccounts.managementLevel": MANAGEMENT_LEVELS,
    **{f"account.{k}": v for k, v in _ACCOUNT_SIDE_FIELDS.items()},
}

# search_accounts: account fields are at the root (no `account.`
# prefix — the root object IS already the account filter).
_ACCOUNT_ENUM_FIELDS: Dict[str, set] = dict(_ACCOUNT_SIDE_FIELDS)

_MISSING = object()


def _dig(obj: Any, path: list[str]):
    """Walk down a dot-path in a nested dict. Returns _MISSING if a
    segment does not exist (absent dict or not a dict)."""
    cur = obj
    for seg in path:
        if not isinstance(cur, dict) or seg not in cur:
            return _MISSING
        cur = cur[seg]
    return cur


def validate_enum_filters(filters: Dict[str, Any] | None, *, scope: str = "contact") -> None:
    """Validate the closed-value fields of a Cognism filters dict.
    Raises `ValueError` with the field, the offending value and the allowed
    values if an out-of-list value is found. Does NOT validate absent
    fields (all optional) nor the dynamic lists
    (regions/countries/.../technologies) — see module docstring.

    Args:
        scope: `"contact"` for a `search_contacts` body (account fields
            under `account.*`), `"account"` for a `search_accounts` body
            (account fields at the root, no prefix).
    """
    if not filters:
        return
    fields = _CONTACT_ENUM_FIELDS if scope == "contact" else _ACCOUNT_ENUM_FIELDS
    for dotted, allowed in fields.items():
        value = _dig(filters, dotted.split("."))
        if value is _MISSING:
            continue
        values = value if isinstance(value, (list, tuple, set)) else [value]
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ValueError(
                f"Cognism filter `{dotted}`: invalid value(s) {bad!r}. "
                f"Allowed: {sorted(allowed)!r}"
            )
