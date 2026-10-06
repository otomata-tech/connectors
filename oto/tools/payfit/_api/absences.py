"""Absences: reading, creating an ALREADY-approved absence, cancelling.

This mixin is never instantiated on its own: it is composed into `PayfitClient`,
which provides the transport (`_get`, `_post`, `_delete`, `_company_path`).

⚠️ **`POST /absences` creates an APPROVED absence**, not a request. The API has
no approval, refusal or balance endpoint: what is written here goes straight
into payroll. Symmetrically, `DELETE` **cancels** the absence and carries its
comment in a JSON BODY — a DELETE with a body, unusual, but that is what the
spec documents.

⚠️ **The two sets of types do not coincide.** What can be READ
(`AbsenceType`, ~50 values, including `other` when PayFit has not yet decided)
and what can be CREATED (`CreateAbsenceType`, ~75 values) overlap without
including each other: creation details the family events (`fr_mariage_salarie`,
`fr_deces_conjoint`…) that reading groups together, and it has neither
`fr_maternite` nor `fr_accident_travail`. Neither list is copied here: an unknown
value comes back as a 400 named by PayFit, whereas a list frozen on the client
side would silently go stale the next time a type is added.

**What the API cannot do**: there is no leave balance, no counter
(paid leave earned/taken, RTT remaining), and no single-absence read.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence, Union

from ..params import clean
from ..params import ident as _id
from ..params import page as _page

# The moments of the day the API accepts at both ends of an absence. A CLOSED set
# on the PayFit side and stable (a half-day has no fourth form):
# refusing it here avoids a 400 that does not name the field.
MOMENTS = ("beginning-of-day", "middle-of-day", "end-of-day")


def _moment(value: Any, name: str) -> str:
    if value not in MOMENTS:
        raise ValueError(f"{name} must be one of {', '.join(MOMENTS)} — "
                         f"got {value!r}.")
    return value


class _AbsencesMixin:
    """Absences: reading, creating, cancelling."""

    def list_absences(self, *, limit: int = 50, cursor: Optional[str] = None,
                      contract_id: Optional[str] = None,
                      status: Optional[Union[str, Sequence[str]]] = None,
                      begin_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/absences.

        Scope `time:read`.

        Args:
            contract_id: only this contract's absences.
            status: approved (upstream default) | pending_approval | declined |
                cancelled | pending_cancellation | all — one or several.
            begin_date / end_date: YYYY-MM-DD; absences overlapping the window.
        """
        if contract_id is not None:
            contract_id = _id(contract_id, "contract_id")
        if status is not None and not isinstance(status, str):
            status = ",".join(status)
        return self._get(self._company_path("/absences"), **_page(limit, cursor),
                         contractId=contract_id, status=status or None,
                         beginDate=begin_date, endDate=end_date)

    def create_absence(self, *, contract_id: str, absence_type: str,
                       start_date: str, end_date: str,
                       start_moment: str = "beginning-of-day",
                       end_moment: str = "end-of-day") -> Any:
        """POST /companies/{companyId}/absences — an already APPROVED absence.

        Scope `time:write`. Returns `{id}`.

        Args:
            contract_id: the contract concerned.
            absence_type: a `CreateAbsenceType` value (`fr_conges_payes`,
                `fr_rtt`, `fr_sans_solde`, `fr_maladie_ordinaire`…) — the exact
                set is the PayFit spec's, not a list kept here.
            start_date / end_date: YYYY-MM-DD.
            start_moment / end_moment: `beginning-of-day`, `middle-of-day` or
                `end-of-day` — the defaults cover an absence in full days.
        """
        return self._post(self._company_path("/absences"), {
            "contractId": _id(contract_id, "contract_id"),
            "type": absence_type,
            "startDate": {"date": start_date,
                          "moment": _moment(start_moment, "start_moment")},
            "endDate": {"date": end_date,
                        "moment": _moment(end_moment, "end_moment")},
        })

    def cancel_absence(self, absence_id: str, *,
                       comment: Optional[str] = None) -> Any:
        """DELETE /companies/{companyId}/absences/{absenceId} — cancels the absence.

        Scope `time:write`. Responds 204 with no body.

        Args:
            comment: comment recorded on the cancellation (optional).
        """
        return self._delete(
            self._company_path(f"/absences/{_id(absence_id, 'absence_id')}"),
            clean({"comment": comment}) or None)
