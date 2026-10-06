"""Employment contracts: reading (two variants), creation, worked time,
health insurance and provident fund affiliation.

This mixin is never instantiated on its own: it is composed into `PayfitClient`,
which provides the transport (`_get`, `_post`, `_put`, `_company_path`).

⚠️ **`/contracts-fr` is not `/contracts` + optional fields**: it is a
different collection, and it is the ONLY one that returns the contract nature
(`natureContratDsn` — 01 CDI, 02 CDD…), the collective-agreement status, the IDCC of the
collective agreement, the termination reason, the working-time arrangement
(`standard`, `forfait_heures`, `forfait_jours`…), the executive-officer status,
the affiliated health insurance and provident fund contracts, and the NIR. For a
French company, it is the one to read; the company's `country`
says so.

⚠️ The `fields=securite-sociale` parameter of `/contracts-fr` is **deprecated** and
is never sent: the NIR now arrives with the key's scope.

**What the API cannot do on a contract**: there is no amendment history,
no modification of an existing contract, no separate classification
coefficient, and no termination endpoint. The only write is CREATION.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ..params import clean
from ..params import ident as _id
from ..params import month as _month
from ..params import page as _page


def _ids(values: Any, name: str) -> list:
    """A list of identifiers, each passed through the same guard as a URL
    segment. They do not travel in the path here, but a malformed id sent in
    the body produces an opaque 400 — better to name it."""
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a LIST of identifiers — got {values!r}.")
    if not values:
        raise ValueError(f"{name} cannot be empty.")
    return [_id(v, name) for v in values]


class _ContractsMixin:
    """Contracts, worked time, health insurance/provident fund affiliations."""

    # --- reading ------------------------------------------------------------

    def list_contracts(self, *, limit: int = 50, cursor: Optional[str] = None,
                       include_in_progress: Optional[bool] = None,
                       fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts (or /contracts-fr when `fr`).

        Scope `contracts:read`. Active, pending and last year's archived
        contracts — **not the company's full history**.

        Args:
            include_in_progress: also contracts still being created.
            fr: the French variant, with DSN fields.
        """
        flag = None if include_in_progress is None else (
            "true" if include_in_progress else "false")
        return self._get(self._company_path("/contracts-fr" if fr else "/contracts"),
                         **_page(limit, cursor), includeInProgressContracts=flag)

    def get_contract(self, contract_id: str, *, fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts/{contractId} (or /contracts-fr/…)."""
        base = "/contracts-fr" if fr else "/contracts"
        return self._get(self._company_path(
            f"{base}/{_id(contract_id, 'contract_id')}"))

    def list_worked_time(self, date: str, *, limit: int = 50,
                         cursor: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/contracts/time — 🇫🇷, the month's time.

        Scope `time:read`. Per contract: `effectiveWorkedTime` (actual),
        `payedWorkedTime` (paid) and `workTimeUnit`.

        ⚠️ This is a **monthly aggregate**, not a schedule: the API serves no
        working hours, no clock-ins, and no individual worked days.

        Args:
            date: the month, AAAAMM.
        """
        return self._get(self._company_path("/contracts/time"),
                         date=_month(date), **_page(limit, cursor))

    # --- creation -----------------------------------------------------------

    def create_contract(self, collaborator_id: str, *, job_title: str,
                        start_date: str) -> Any:
        """POST /companies/{companyId}/collaborators/{collaboratorId}/contracts — 🇫🇷.

        Scope `collaborators:contracts:write`. Creates the contract of an
        already existing collaborator; it is what brings them into payroll.

        Args:
            job_title: the job title.
            start_date: YYYY-MM-DD.
        """
        col = _id(collaborator_id, "collaborator_id")
        return self._post(self._company_path(f"/collaborators/{col}/contracts"),
                          {"jobTitle": job_title, "startDate": start_date})

    # --- health insurance and provident fund of ONE contract -----------------

    def set_health_insurance(self, contract_id: str, *,
                             health_insurance_contract_ids: Sequence[str],
                             employee_is_exempted: Optional[bool] = None) -> Any:
        """PUT /companies/{companyId}/contracts-fr/{contractId}/health-insurance.

        Scope `health-insurance:write`. **Replaces** the contract's health
        insurance affiliation with the list provided: it is not an addition, a
        list that omits a contract unaffiliates it.

        Args:
            health_insurance_contract_ids: ids from
                `list_health_insurance_contracts`.
            employee_is_exempted: the employee is exempt from joining.
        """
        con = _id(contract_id, "contract_id")
        return self._put(
            self._company_path(f"/contracts-fr/{con}/health-insurance"),
            clean({"healthInsuranceContractIds": _ids(
                health_insurance_contract_ids, "health_insurance_contract_ids"),
                "employeeIsExempted": employee_is_exempted}))

    def set_provident_fund(self, contract_id: str, *,
                           provident_fund_contract_ids: Sequence[str]) -> Any:
        """PUT /companies/{companyId}/contracts-fr/{contractId}/provident-fund.

        Scope `health-insurance:write` (the same as health insurance). **Replaces**
        the contract's provident fund affiliation.

        Args:
            provident_fund_contract_ids: ids from `list_provident_fund_contracts`.
        """
        con = _id(contract_id, "contract_id")
        return self._put(
            self._company_path(f"/contracts-fr/{con}/provident-fund"),
            {"providentFundContractIds": _ids(
                provident_fund_contract_ids, "provident_fund_contract_ids")})

    def request_health_insurance_regularization(
            self, contract_id: str, *,
            health_insurance_contract_ids: Sequence[str],
            effective_date: str) -> Any:
        """POST /companies/{companyId}/contracts-fr/{contractId}/regularization.

        Scope `health-insurance:write`. Requests a retroactive regularization
        of health insurance contributions: it **recalculates contributions already
        passed through payroll** and flows into a payslip.

        Args:
            effective_date: YYYY-MM-DD, the regularization's effective date.
        """
        con = _id(contract_id, "contract_id")
        return self._post(
            self._company_path(f"/contracts-fr/{con}/regularization"),
            {"healthInsuranceContractIds": _ids(
                health_insurance_contract_ids, "health_insurance_contract_ids"),
             "effectiveDate": effective_date})
