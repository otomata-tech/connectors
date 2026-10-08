"""Silae periods and dates — the conversion of a caller's month into the field's format.

A Silae pay period is a MONTH. The API reads it in two shapes, depending on the field:

- `string(date-time)` (`periode`, `periodeDebut`, `periodeFin`…): first day of the month,
  `AAAA-MM-01T00:00:00` — only the year and the month are read;
- `string(date)` (`periode` of the DSN and declaration functions): `AAAA-MM-01`.

A caller passes the month as `AAAA-MM`. The first-of-month forms Silae itself returns
(`AAAA-MM-01`, `AAAA-MM-01T00:00:00`, e.g. `DossierRecupererPeriodeEnCours`) are accepted
too, so a value read from the API can be passed back as is. Any other day is refused: a
pay period has no day, and silently truncating one would answer for a month the caller
did not name.
"""
from __future__ import annotations

import re
from datetime import date

_MOIS = re.compile(r"^(\d{4})-(\d{2})(?:-01(?:T00:00:00(?:\.0+)?Z?)?)?$")

#: Range limit documented by Silae for cumulative totals and the contributions report.
MAX_MOIS = 12


def mois(valeur: str, champ: str = "periode") -> tuple[int, int]:
    """`(year, month)` of a pay period given as `AAAA-MM` (or a first-of-month date)."""
    m = _MOIS.match(str(valeur).strip()) if valeur is not None else None
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(
            f"{champ}: expected a pay month `AAAA-MM` (e.g. 2026-05), got {valeur!r}")
    return int(m.group(1)), int(m.group(2))


def periode_datetime(valeur: str, champ: str = "periode") -> str:
    """A pay month for a `string(date-time)` field: `AAAA-MM-01T00:00:00`."""
    an, mo = mois(valeur, champ)
    return f"{an:04d}-{mo:02d}-01T00:00:00"


def periode_date(valeur: str, champ: str = "periode") -> str:
    """A pay month for a `string(date)` field: `AAAA-MM-01`."""
    an, mo = mois(valeur, champ)
    return f"{an:04d}-{mo:02d}-01"


def date_jour(valeur: str, champ: str = "date") -> str:
    """A calendar day `AAAA-MM-JJ`, validated, for a field that reads the exact date."""
    try:
        return date.fromisoformat(str(valeur).strip()).isoformat()
    except ValueError:
        raise ValueError(f"{champ}: expected a date `AAAA-MM-JJ`, got {valeur!r}") from None


def plage(debut: str, fin: str, *, max_mois: int | None = None) -> tuple[str, str]:
    """`(periodeDebut, periodeFin)` as date-times, checked: the end is not before the
    start and, when `max_mois` is set, the range (both months included) does not
    exceed it — Silae refuses a longer one (its error 13)."""
    a = mois(debut, "periode_debut")
    b = mois(fin, "periode_fin")
    n = (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1
    if n < 1:
        raise ValueError(f"periode_fin ({fin}) is before periode_debut ({debut})")
    if max_mois is not None and n > max_mois:
        raise ValueError(
            f"the range {debut} → {fin} covers {n} months; Silae accepts {max_mois} at "
            "most for this function — split it into several calls")
    return periode_datetime(debut, "periode_debut"), periode_datetime(fin, "periode_fin")
