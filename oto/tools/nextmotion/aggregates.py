"""Nextmotion — clientele aggregates for a clinic, never a patient row.

Turns the raw rows `NextmotionClient.list_patients` pages (`_api/patients.py`)
into clinic-level counts and distributions — age band, sex, and either top
zip codes or top French départements — and never surfaces an individual
patient, not even to its own caller. This module does not call the API
itself (it takes rows, already fetched and concatenated across pages): that
keeps it testable without a client or a key, and keeps the privacy floor
below in ONE place regardless of who paginates.

Privacy floor, not a formality
------------------------------
Any group small enough to re-identify someone — an age band × sex cell, a
top-geography bucket, or the clinic's median age / sex split when the whole
clinic is tiny — is DROPPED rather than shown with a small count: a
"1 patiente de 85 ans dans le 75016" line re-identifies someone even though
no name was ever returned. A suppressed cell does not get folded into an
"autre" bucket either — that would still leak the true total by
subtraction.

`min_group_size` (default 10) is OUR default, not a legal or regulatory
threshold: pick the right one for the clinic's own re-identification risk
(smaller clinics, rarer age bands and finer geography all call for a higher
floor) and say, to whoever reads the numbers, that it is a choice.

Field names (`birth_date`, `gender`, `zip_code`) come from the Nextmotion
patient schema documented on the write side of this same resource
(`create_patient`/`update_patient` in `connectors/nextmotion/connector.yaml`):
`gender` is `0` female, `1` male, `2` other; `birth_date` is `YYYY-MM-DD`. A
row missing one of them is a data-shape surprise, not a silent zero — this
raises rather than guess.

Period grouping (optional, by the caller's field)
--------------------------------------------------
Nextmotion's `list_patients` exposes no registration-date filter today (see
`_api/patients.py`), so this module cannot assume a period field exists or
name it. If the rows a caller fetched DO carry a usable date field (e.g. a
`created_at` the API happens to return), pass `period_field` and this
groups by the leading `period_prefix_len` characters of that field's value
(8 → `YYYY-MM-`, 4 → `YYYY`); omit it for a single clinic-wide aggregate.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

_GENDER_LABELS = {0: "female", 1: "male", 2: "other"}

#: (inclusive low, exclusive high); the last band's high (200) is a sentinel above
#: any age `_age_from_birth_date` can return (capped at 130) so it reads as open-ended.
DEFAULT_AGE_BANDS: Tuple[Tuple[int, int], ...] = (
    (0, 18), (18, 30), (30, 45), (45, 60), (60, 75), (75, 200),
)

#: Default per the module docstring — a choice, not a norm.
DEFAULT_MIN_GROUP_SIZE = 10


def _age_band(age: int, bands: Sequence[Tuple[int, int]]) -> str:
    for lo, hi in bands:
        if lo <= age < hi:
            return f"{lo}+" if hi >= 200 else f"{lo}-{hi - 1}"
    raise ValueError(f"age {age} ne tombe dans aucune tranche de {bands!r}.")


def _age_from_birth_date(birth_date: str, *, as_of: date) -> int:
    try:
        y, m, d = (int(p) for p in birth_date.split("-"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"birth_date {birth_date!r} n'est pas au format YYYY-MM-DD."
        ) from exc
    age = as_of.year - y - ((as_of.month, as_of.day) < (m, d))
    if not 0 <= age <= 130:
        raise ValueError(f"birth_date {birth_date!r} donne un âge incohérent ({age}).")
    return age


def _department_from_zip(zip_code: str) -> str:
    """French département from a 5-digit zip code. DOM (971-976) keep 3 digits;
    Corse (20xxx) splits 2A/2B by the usual zip-range convention — this is a
    heuristic on the zip, not the INSEE commune code, and can be wrong at the
    Corse boundary for a handful of communes; good enough for a distribution,
    not for naming an individual commune."""
    z = (zip_code or "").strip()
    if not z.isdigit() or len(z) != 5:
        raise ValueError(f"zip_code {zip_code!r} n'est pas un code postal français à 5 chiffres.")
    if z[:2] in ("97", "98"):
        return z[:3]
    if z[:2] == "20":
        return "2A" if int(z) < 20200 else "2B"
    return z[:2]


def _suppress(counts: Dict[str, int], min_group_size: int) -> Dict[str, int]:
    return {k: v for k, v in counts.items() if v >= min_group_size}


def _period_key(row: Dict[str, Any], period_field: Optional[str], prefix_len: int) -> Optional[str]:
    if period_field is None:
        return None
    try:
        value = row[period_field]
    except KeyError as exc:
        raise ValueError(
            f"period_field={period_field!r} absent d'une rangée patient."
        ) from exc
    return str(value)[:prefix_len]


def aggregate_patients(
    rows: Iterable[Dict[str, Any]],
    *,
    geography: Literal["zip_code", "department"] = "department",
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
    age_bands: Optional[Sequence[Tuple[int, int]]] = None,
    top_geography: int = 10,
    as_of: Optional[date] = None,
    period_field: Optional[str] = None,
    period_prefix_len: int = 8,
) -> Dict[str, Any]:
    """Aggregate clinic patient rows into counts and distributions only.

    Never returns a patient row, an id, a name or any field not listed in the
    output below. Raises on a row missing `birth_date`, `gender` or
    `zip_code` rather than silently skipping it or guessing a value — a data
    shape this module does not recognise is a bug to fix, not a count to get
    quietly wrong.

    Returns one aggregate (period=None key) when `period_field` is omitted,
    or one aggregate per period bucket otherwise. Each aggregate has:
    - `total_patients`: the clinic (or clinic × period) headcount — not
      itself suppressed (see module docstring: suppress the SMALL groups a
      total is built from, not the total itself);
    - `median_age`, `gender_distribution`: `None` / `{}` if
      `total_patients < min_group_size` (a median or a 2-way split on a
      handful of people narrows down who they are almost as much as a row
      would);
    - `age_gender_distribution`: list of `{age_band, gender, count}`, bands
      with a count `< min_group_size` dropped;
    - `top_geography`: list of `{geography, count}` (zip or département per
      `geography`), buckets `< min_group_size` dropped, longest first,
      truncated to `top_geography` entries.
    """
    bands = tuple(age_bands) if age_bands is not None else DEFAULT_AGE_BANDS
    as_of = as_of or date.today()

    by_period: Dict[Optional[str], Dict[str, Any]] = defaultdict(lambda: {
        "ages": [], "gender_counts": Counter(), "age_gender_counts": Counter(),
        "geo_counts": Counter(),
    })

    for row in rows:
        try:
            birth_date = row["birth_date"]
            gender_code = row["gender"]
            zip_code = row["zip_code"]
        except KeyError as exc:
            raise ValueError(
                f"Rangée patient sans champ {exc} (attendu birth_date/gender/zip_code "
                "— voir list_patients)."
            ) from exc
        if gender_code not in _GENDER_LABELS:
            raise ValueError(f"gender {gender_code!r} inattendu (attendu 0, 1 ou 2).")

        gender = _GENDER_LABELS[gender_code]
        age = _age_from_birth_date(birth_date, as_of=as_of)
        band = _age_band(age, bands)
        geo = zip_code if geography == "zip_code" else _department_from_zip(zip_code)
        period = _period_key(row, period_field, period_prefix_len)

        bucket = by_period[period]
        bucket["ages"].append(age)
        bucket["gender_counts"][gender] += 1
        bucket["age_gender_counts"][(band, gender)] += 1
        bucket["geo_counts"][geo] += 1

    results: Dict[Optional[str], Dict[str, Any]] = {}
    for period, bucket in by_period.items():
        total = len(bucket["ages"])
        enough = total >= min_group_size
        results[period] = {
            "total_patients": total,
            "min_group_size": min_group_size,
            "geography": geography,
            "median_age": statistics.median(bucket["ages"]) if enough else None,
            "gender_distribution": _suppress(dict(bucket["gender_counts"]), min_group_size) if enough else {},
            "age_gender_distribution": [
                {"age_band": band, "gender": gender, "count": count}
                for (band, gender), count in sorted(bucket["age_gender_counts"].items())
                if count >= min_group_size
            ],
            "top_geography": [
                {"geography": geo, "count": count}
                for geo, count in bucket["geo_counts"].most_common()
                if count >= min_group_size
            ][:top_geography],
            "suppressed_note": (
                "Tout groupe (tranche d'âge x sexe, zone géographique, ou le total "
                f"de ce groupe) de moins de {min_group_size} patients est retiré de "
                "cette réponse — jamais de ligne patient individuelle. Seuil par "
                "défaut, pas une norme imposée : à ajuster au risque de "
                "ré-identification propre à chaque clinique."
            ),
        }

    _empty = {
        "total_patients": 0,
        "min_group_size": min_group_size,
        "geography": geography,
        "median_age": None,
        "gender_distribution": {},
        "age_gender_distribution": [],
        "top_geography": [],
        "suppressed_note": f"Aucun patient en entrée — rien à agréger (seuil {min_group_size}).",
    }
    if period_field is None:
        return results.get(None, _empty)
    return {"by_period": results}


def clinic_patient_aggregates(
    client: Any,
    clinic_id: str,
    *,
    is_archived: Optional[bool] = None,
    geography: Literal["zip_code", "department"] = "department",
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
    age_bands: Optional[Sequence[Tuple[int, int]]] = None,
    top_geography: int = 10,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Page through `client.list_patients(clinic_id, ...)` and aggregate.

    `client` is a `NextmotionClient` (or anything exposing the same
    `list_patients(clinic_id, *, is_archived, limit, offset)` — this module
    only calls that one method). Paginates until a page is shorter than
    `page_size`, mirroring the `{count, next, previous, data}` shape documented
    in `connectors/nextmotion/connector.yaml`.
    """
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = client.list_patients(clinic_id, is_archived=is_archived,
                                    limit=page_size, offset=offset)
        batch = page["data"] if isinstance(page, dict) and "data" in page else page
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return aggregate_patients(rows, geography=geography, min_group_size=min_group_size,
                              age_bands=age_bands, top_geography=top_geography)
