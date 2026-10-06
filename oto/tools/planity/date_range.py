"""Date range parsing used by all stats/listing tools.

Accepts:
- Named aliases: "today", "yesterday", "this_week", "last_week", "this_month",
  "last_month", "7d", "30d", "90d", "ytd"
- ISO date strings: "2026-04-16" / "2026-04-16T10:00:00"
- Mixed: date_from="2026-04-01", date_to="today"

Returns (gte_ms, lte_ms) as unix millisecond timestamps — Planity's convention.
Timezone: Europe/Paris (where the business operates).
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# No fallback to a fixed offset: `+02:00` would be wrong half the year, and an
# interval shifted by an hour goes unnoticed — it reads as a revenue figure. A
# system without a timezone database raises here, at import, where it is
# visible (and is fixed by installing `tzdata`).
FR_TZ = ZoneInfo("Europe/Paris")


def _day_bounds(d: date) -> Tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=FR_TZ)
    end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=FR_TZ)
    return start, end


def _today_in_fr() -> date:
    return datetime.now(tz=FR_TZ).date()


def _parse_iso(s: str) -> datetime:
    """Parse ISO date or datetime, assume FR_TZ if no tz."""
    if len(s) == 10:  # YYYY-MM-DD
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=FR_TZ)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FR_TZ)
    return dt.astimezone(FR_TZ)


def resolve_range(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    preset: Optional[str] = None,
) -> Tuple[int, int]:
    """Resolve a period to (gte_ms, lte_ms).

    Precedence:
    1. If preset is set (e.g. "7d"), it overrides date_from/date_to.
    2. Otherwise date_from/date_to (either can be a preset keyword too).
    3. If nothing is set, default to last 7 days.
    """
    if preset:
        return _preset_to_range(preset)
    if date_from is None and date_to is None:
        return _preset_to_range("7d")
    # Interpret date_from/date_to — allow preset keywords as shortcuts
    if date_from in _PRESETS or date_to in _PRESETS:
        # If either is a preset, use the preset bounds entirely
        ref = date_from if date_from in _PRESETS else date_to
        return _preset_to_range(ref)  # type: ignore[arg-type]
    start_dt = _parse_iso(date_from) if date_from else datetime.now(FR_TZ) - timedelta(days=7)
    end_dt = _parse_iso(date_to) if date_to else datetime.now(FR_TZ)
    if len(date_to or "") == 10:
        # End-of-day for date-only "to"
        end_dt = end_dt.replace(hour=23, minute=59, second=59)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def _preset_to_range(preset: str) -> Tuple[int, int]:
    now = datetime.now(FR_TZ)
    today = now.date()
    if preset == "today":
        s, e = _day_bounds(today)
        return int(s.timestamp()*1000), int(now.timestamp()*1000)
    if preset == "yesterday":
        d = today - timedelta(days=1)
        s, e = _day_bounds(d)
        return int(s.timestamp()*1000), int(e.timestamp()*1000)
    if preset in ("this_week", "week"):
        monday = today - timedelta(days=today.weekday())
        s, _ = _day_bounds(monday)
        return int(s.timestamp()*1000), int(now.timestamp()*1000)
    if preset == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)
        s, _ = _day_bounds(last_monday)
        _, e = _day_bounds(last_sunday)
        return int(s.timestamp()*1000), int(e.timestamp()*1000)
    if preset in ("this_month", "month"):
        first = today.replace(day=1)
        s, _ = _day_bounds(first)
        return int(s.timestamp()*1000), int(now.timestamp()*1000)
    if preset == "last_month":
        first_of_this = today.replace(day=1)
        last_of_prev = first_of_this - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        s, _ = _day_bounds(first_of_prev)
        _, e = _day_bounds(last_of_prev)
        return int(s.timestamp()*1000), int(e.timestamp()*1000)
    if preset == "ytd":
        first = today.replace(month=1, day=1)
        s, _ = _day_bounds(first)
        return int(s.timestamp()*1000), int(now.timestamp()*1000)
    if preset.endswith("d") and preset[:-1].isdigit():
        days = int(preset[:-1])
        start = now - timedelta(days=days)
        return int(start.timestamp()*1000), int(now.timestamp()*1000)
    raise ValueError(f"Unknown date preset: {preset!r}")


_PRESETS = {
    "today", "yesterday", "this_week", "week", "last_week",
    "this_month", "month", "last_month", "ytd",
    "7d", "14d", "30d", "60d", "90d", "180d", "365d",
}


def ms_to_iso(ms: int | float | None) -> Optional[str]:
    if ms is None or ms == 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=FR_TZ).isoformat(timespec="seconds")
