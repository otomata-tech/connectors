"""Tests — Nextmotion clientele aggregates: never a row, always suppressed
below the group-size floor."""
from __future__ import annotations

from datetime import date

import pytest

from oto.tools.nextmotion.aggregates import (
    DEFAULT_MIN_GROUP_SIZE,
    _age_band,
    _age_from_birth_date,
    _department_from_zip,
    aggregate_patients,
    clinic_patient_aggregates,
)

AS_OF = date(2026, 10, 6)


def _patient(birth_date: str, gender: int, zip_code: str, **extra) -> dict:
    return {"birth_date": birth_date, "gender": gender, "zip_code": zip_code, **extra}


def _cohort(n: int, birth_date: str, gender: int, zip_code: str) -> list[dict]:
    return [_patient(birth_date, gender, zip_code) for _ in range(n)]


def test_age_from_birth_date_handles_birthday_boundary():
    assert _age_from_birth_date("2000-10-06", as_of=AS_OF) == 26
    assert _age_from_birth_date("2000-10-07", as_of=AS_OF) == 25


def test_age_from_birth_date_rejects_bad_shape():
    with pytest.raises(ValueError):
        _age_from_birth_date("not-a-date", as_of=AS_OF)


def test_age_band_boundaries():
    assert _age_band(0, ((0, 18), (18, 200))) == "0-17"
    assert _age_band(17, ((0, 18), (18, 200))) == "0-17"
    assert _age_band(18, ((0, 18), (18, 200))) == "18+"
    assert _age_band(130, ((0, 18), (18, 200))) == "18+"


def test_department_from_zip_mainland_dom_corse():
    assert _department_from_zip("75016") == "75"
    assert _department_from_zip("97400") == "974"
    assert _department_from_zip("20090") == "2A"
    assert _department_from_zip("20600") == "2B"


def test_department_from_zip_rejects_malformed():
    with pytest.raises(ValueError):
        _department_from_zip("ABCDE")
    with pytest.raises(ValueError):
        _department_from_zip("123")


def test_no_patient_row_or_identifying_field_in_output():
    rows = _cohort(DEFAULT_MIN_GROUP_SIZE, "1990-01-01", 0, "75016")
    out = aggregate_patients(rows, as_of=AS_OF)
    dumped = str(out)
    assert "1990-01-01" not in dumped
    assert set(out) == {
        "total_patients", "min_group_size", "geography", "median_age",
        "gender_distribution", "age_gender_distribution", "top_geography",
        "suppressed_note",
    }


def test_group_below_threshold_is_dropped_not_zeroed():
    # 9 patients, one bucket — below the default floor of 10: nothing in the
    # distributions, but the clinic-wide total headcount still surfaces, and
    # the median/sex split (which would narrow 9 people down) do not.
    rows = _cohort(DEFAULT_MIN_GROUP_SIZE - 1, "1990-01-01", 0, "75016")
    out = aggregate_patients(rows, as_of=AS_OF)
    assert out["total_patients"] == DEFAULT_MIN_GROUP_SIZE - 1
    assert out["median_age"] is None
    assert out["gender_distribution"] == {}
    assert out["age_gender_distribution"] == []
    assert out["top_geography"] == []


def test_group_at_threshold_is_kept():
    rows = _cohort(DEFAULT_MIN_GROUP_SIZE, "1990-01-01", 0, "75016")
    out = aggregate_patients(rows, as_of=AS_OF)
    assert out["total_patients"] == DEFAULT_MIN_GROUP_SIZE
    assert out["median_age"] == 36
    assert out["gender_distribution"] == {"female": DEFAULT_MIN_GROUP_SIZE}
    assert out["age_gender_distribution"] == [
        {"age_band": "30-44", "gender": "female", "count": DEFAULT_MIN_GROUP_SIZE},
    ]
    assert out["top_geography"] == [{"geography": "75", "count": DEFAULT_MIN_GROUP_SIZE}]


def test_mixed_cohort_suppresses_small_cells_keeps_large_ones():
    rows = (
        _cohort(12, "1990-01-01", 0, "75016")  # female, 30-44, dept 75 → kept
        + _cohort(3, "1950-01-01", 1, "75016")  # male, 75+, dept 75 → cell dropped
    )
    out = aggregate_patients(rows, as_of=AS_OF)
    assert out["total_patients"] == 15
    # geography bucket 75 sums both cohorts (12 + 3 = 15 >= threshold) → kept
    assert out["top_geography"] == [{"geography": "75", "count": 15}]
    # but the small age/gender cell (3 males, 75+) must not appear
    bands = {(e["age_band"], e["gender"]): e["count"] for e in out["age_gender_distribution"]}
    assert ("75+", "male") not in bands
    assert bands[("30-44", "female")] == 12


def test_unknown_gender_code_raises():
    with pytest.raises(ValueError):
        aggregate_patients([_patient("1990-01-01", 9, "75016")], as_of=AS_OF)


def test_missing_field_raises_rather_than_silently_skips():
    with pytest.raises(ValueError):
        aggregate_patients([{"birth_date": "1990-01-01", "gender": 0}], as_of=AS_OF)


def test_zip_code_geography_mode():
    rows = _cohort(10, "1990-01-01", 0, "75016")
    out = aggregate_patients(rows, geography="zip_code", as_of=AS_OF)
    assert out["top_geography"] == [{"geography": "75016", "count": 10}]


def test_top_geography_truncates():
    rows = []
    for i, zip_code in enumerate(["75001", "75002", "75003", "75004"]):
        rows += _cohort(10 + i, "1990-01-01", 0, zip_code)
    out = aggregate_patients(rows, geography="zip_code", top_geography=2, as_of=AS_OF)
    assert len(out["top_geography"]) == 2
    assert out["top_geography"][0]["count"] >= out["top_geography"][1]["count"]


def test_period_grouping_by_caller_supplied_field():
    rows = (
        _cohort(10, "1990-01-01", 0, "75016")[0:10]
    )
    for r in rows[:5]:
        r["created_at"] = "2026-01-15T00:00:00Z"
    for r in rows[5:]:
        r["created_at"] = "2026-02-15T00:00:00Z"
    out = aggregate_patients(rows, period_field="created_at", period_prefix_len=7, as_of=AS_OF)
    assert set(out["by_period"]) == {"2026-01", "2026-02"}
    assert out["by_period"]["2026-01"]["total_patients"] == 5


def test_no_patients_returns_empty_shape():
    out = aggregate_patients([], as_of=AS_OF)
    assert out["total_patients"] == 0
    assert out["median_age"] is None


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def list_patients(self, clinic_id, *, is_archived=None, limit=100, offset=0):
        self.calls.append((clinic_id, is_archived, limit, offset))
        page_index = offset // limit
        return {"data": self._pages[page_index]} if page_index < len(self._pages) else {"data": []}


def test_clinic_patient_aggregates_paginates_until_short_page():
    page1 = _cohort(100, "1990-01-01", 0, "75016")
    page2 = _cohort(5, "1990-01-01", 0, "75016")
    client = _FakeClient([page1, page2])
    out = clinic_patient_aggregates(client, "c1", page_size=100)
    assert out["total_patients"] == 105
    assert client.calls == [("c1", None, 100, 0), ("c1", None, 100, 100)]


def test_clinic_patient_aggregates_single_short_page_stops():
    client = _FakeClient([_cohort(3, "1990-01-01", 0, "75016")])
    out = clinic_patient_aggregates(client, "c1", page_size=100)
    assert out["total_patients"] == 3
    assert len(client.calls) == 1
