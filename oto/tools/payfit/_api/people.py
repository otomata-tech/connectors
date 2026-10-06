"""People: the collaborator directory, their payslips, their meal vouchers.

This mixin is never instantiated on its own: it is composed into `PayfitClient`,
which provides the transport (`_get`, `_get_file`, `_post`, `_company_path`).

⚠️ **A payslip is two calls and two natures.** `list_payslips` returns the
METADATA (`year`, `month`, `contractId`, `payslipId`, `payslipUrl`);
`get_payslip` returns the PDF. **The API never serves the LINES of a payslip**:
gross, net, contribution by contribution, no endpoint returns them
as data. The only structured amounts in this API are the accounting entries
(`list_accounting_entries`).

⚠️ `list_payslips` neither paginates nor filters: it returns ALL of the
collaborator's payslips, across all contracts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..params import clean
from ..params import ident as _id
from ..params import month as _month
from ..params import page as _page


class _PeopleMixin:
    """Collaborators, payslips, meal vouchers."""

    # --- directory ----------------------------------------------------------

    def list_collaborators(self, *, limit: int = 50, cursor: Optional[str] = None,
                           email: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/collaborators.

        Scope `collaborators:read`; sensitive fields (NIR, IBAN/BIC,
        birth, personal contact details) only arrive if the key ALSO carries
        `collaborators:social-security:read`, `collaborators:bank-info:read`,
        `collaborators:personal:read` or `collaborators:legal-identity:read`.

        Args:
            email: only collaborators with this email in one of their contracts
                (the login email is not searched).
        """
        return self._get(self._company_path("/collaborators"),
                         **_page(limit, cursor), email=email)

    def get_collaborator(self, collaborator_id: str) -> Any:
        """GET /companies/{companyId}/collaborators/{collaboratorId}."""
        return self._get(self._company_path(
            f"/collaborators/{_id(collaborator_id, 'collaborator_id')}"))

    def create_collaborator(self, *, first_name: str, last_name: str,
                            personal_email: str, other_name: Optional[str] = None,
                            social_security_number: Optional[str] = None,
                            personal_address: Optional[dict] = None,
                            birth_information: Optional[dict] = None,
                            personal_phone_number: Optional[str] = None,
                            number_of_children: Optional[int] = None,
                            gender: Optional[str] = None,
                            invite_collaborator: Optional[bool] = None) -> Any:
        """POST /companies/{companyId}/collaborators — creates a collaborator.

        Scope `collaborators:write`. Returns `{collaboratorId}`. A collaborator
        created here has **no contract yet**: `create_contract` is the next
        step, and it is what brings them into payroll.

        ⚠️ `invite_collaborator=True` **sends an email** to the person so
        they can create their PayFit access: an effect visible outside the system.

        Args:
            first_name / last_name / personal_email: the three required fields.
            other_name: usage name (FR), second surname (ES), middle name (UK).
            social_security_number: NIR — length imposed per country (FR 15,
                ES 14, GB 12).
            personal_address: `{streetNumber, addressFirstLine, addressSecondLine,
                city, state, postCode, country}` — `country` as a 2-letter ISO
                code; `streetNumber`, `addressFirstLine`, `city`, `postCode`
                and `country` are required as soon as the object is provided.
            birth_information: `{birthDate, birthPlace, birthCountry}`,
                `birthDate` as YYYY-MM-DD in the past.
            number_of_children: integer from 0 to 20.
            gender: `MALE` or `FEMALE` — the API's CLOSED set, not ours.
            invite_collaborator: sends the invitation email.
        """
        return self._post(self._company_path("/collaborators"), clean({
            "firstName": first_name, "lastName": last_name,
            "personalEmail": personal_email, "otherName": other_name,
            "socialSecurityNumber": social_security_number,
            "personalAddress": personal_address,
            "birthInformation": birth_information,
            "personalPhoneNumber": personal_phone_number,
            "numberOfChildren": number_of_children, "gender": gender,
            "inviteCollaborator": invite_collaborator,
        }))

    # --- payslips -----------------------------------------------------------

    def list_payslips(self, collaborator_id: str) -> Any:
        """GET /companies/{companyId}/collaborators/{collaboratorId}/payslips.

        Scope `contracts:payslips:read`. Returns `{payslips: [{year, month,
        contractId, payslipId, payslipUrl}]}` — metadata, never
        amounts.
        """
        return self._get(self._company_path(
            f"/collaborators/{_id(collaborator_id, 'collaborator_id')}/payslips"))

    def get_payslip(self, collaborator_id: str, contract_id: str,
                    payslip_id: str) -> Dict[str, Any]:
        """GET …/collaborators/{id}/contracts/{id}/payslips/{id} — the PDF.

        Scope `contracts:payslips:read`. Returns `{data: bytes, filename, mimetype}`.
        The three identifiers come from a single `list_payslips` entry
        (plus the collaborator id): a row's `contractId` is not
        interchangeable with another of the person's contracts.
        """
        col = _id(collaborator_id, "collaborator_id")
        con = _id(contract_id, "contract_id")
        pay = _id(payslip_id, "payslip_id")
        return self._get_file(
            self._company_path(f"/collaborators/{col}/contracts/{con}/payslips/{pay}"),
            filename=f"payfit-bulletin-{pay}.pdf", mimetype="application/pdf")

    # --- meal vouchers ------------------------------------------------------

    def list_meal_vouchers(self, date: str, *, limit: int = 50,
                           cursor: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/collaborators/meal-vouchers — 🇫🇷.

        Scope `collaborators:meal-vouchers:read`. Per collaborator and for the
        month: number of vouchers, face value, employer share, employee share,
        eligibility of non-worked days.

        Args:
            date: the month, AAAAMM.
        """
        return self._get(self._company_path("/collaborators/meal-vouchers"),
                         date=_month(date), **_page(limit, cursor))
