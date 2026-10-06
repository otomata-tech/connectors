"""The company and the PAYROLL of a month: cycle status, accounting entries,
accounting export, payment file, documents, health insurance and provident fund.

This mixin is never instantiated on its own: it is composed into `PayfitClient`,
which provides the transport (`_get`, `_get_file`, `_company_path`).

⚠️ **Two collection roots, not a root and a suffix**: the "world" company lives
under `/companies/{id}`, the French variant under `/companies-fr/{id}`. The
latter adds SIREN, SIRET, the legal address and the health insurance proration
method; it does not replace the former.

⚠️ **`accounting` and `accounting-v2` are not two versions of the same format**:
`accounting` returns a FILE (semicolon-separated CSV, as the accounting firm
imports it), `accounting-v2` returns the SAME entries as JSON, line by line
(`operationDate`, `accountId`, `accountName`, `debit`, `credit`, `contractId`,
`employeeFullName`, analytical codes). What an agent can analyze is v2;
what an accountant imports is v1. Both are served.

Employer cost and contributions have **no endpoint of their own**: they are read
from the entries (accounts 641x, 645x, 6417x for benefits in kind) —
it is the only route the API documents.
"""
from __future__ import annotations

from typing import Any, Dict

from ..params import month as _month
from ..params import ident as _id


class _PayrollMixin:
    """Company, payroll cycle, accounting, documents, health insurance/provident fund."""

    # --- company ------------------------------------------------------------

    def get_company(self, *, fr: bool = False) -> Any:
        """GET /companies/{companyId} — or /companies-fr/{companyId} if `fr`.

        No scope required. The FR variant adds `siren`, `siret`, the legal
        address and `healthInsuranceProrationMethod`.
        """
        return self._get(self._company_path(fr=fr))

    def get_payroll_status(self, date: str) -> Any:
        """GET /companies/{companyId}/payroll-status — `{status, executionEndDate}`.

        `status` is `completed` or `not_completed`: it tells whether the month's
        figures are final. No scope required.

        Args:
            date: the month, AAAAMM.
        """
        return self._get(self._company_path("/payroll-status"), date=_month(date))

    # --- accounting and payments --------------------------------------------

    def list_accounting_entries(self, date: str) -> Any:
        """GET /companies/{companyId}/accounting-v2 — the month's entries, as JSON.

        Scope `accounting:read`. Returns a bare ARRAY (no `meta` envelope) and
        does not paginate: the whole month arrives at once.

        Args:
            date: the month, AAAAMM.
        """
        return self._get(self._company_path("/accounting-v2"), date=_month(date))

    def get_accounting_export(self, date: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/accounting — the accounting journal as a FILE.

        Scope `accounting:read`. Returns `{data: bytes, filename, mimetype}`.

        Args:
            date: the month, AAAAMM.
        """
        m = _month(date)
        return self._get_file(self._company_path("/accounting"),
                              filename=f"payfit-comptabilite-{m}.csv",
                              mimetype="text/csv", date=m)

    def get_payment_file(self, date: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/payment-files — the month's payment file.

        Scope `payment-files:read`. Returns `{data: bytes, filename, mimetype}`.

        ⚠️ This file carries the **employees' bank details**: it is a transfer
        order meant for a bank, not a management report.

        Args:
            date: the month, AAAAMM.
        """
        m = _month(date)
        return self._get_file(self._company_path("/payment-files"),
                              filename=f"payfit-virements-{m}.txt",
                              mimetype="application/octet-stream", date=m)

    # --- documents ----------------------------------------------------------

    def get_document(self, document_id: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/documents/{documentId} — a company PDF.

        Scope `contracts:payslips:read` or `pension:read` depending on the
        document. Returns `{data: bytes, filename, mimetype}`. This is the way to
        retrieve the `documentId`s returned by `list_income_tax_documents` and
        `list_auto_enrolment_documents`.
        """
        doc = _id(document_id, "document_id")
        return self._get_file(self._company_path(f"/documents/{doc}"),
                              filename=f"payfit-{doc}.pdf", mimetype="application/pdf")

    def list_income_tax_documents(self) -> Any:
        """GET /companies/{companyId}/income-taxes-documents — 🇬🇧 only.

        Scope `contracts:payslips:read`. UK tax documents (P45,
        P60…): `{documents: [{documentId, type, year, month, contractId,
        documentUrl, createdAt}]}`. **No French equivalent**: the API serves
        neither DSN nor FR tax certificates.
        """
        return self._get(self._company_path("/income-taxes-documents"))

    def list_auto_enrolment_documents(self) -> Any:
        """GET /companies/{companyId}/auto-enrolment-documents — 🇬🇧 only.

        Scope `pension:read`. UK pension auto-enrolment documents.
        """
        return self._get(self._company_path("/auto-enrolment-documents"))

    # --- health insurance and provident fund (company catalogue) ------------

    def list_health_insurance_contracts(self) -> Any:
        """GET /companies/{companyId}/health-insurance-contracts — 🇫🇷.

        Scope `health-insurance:read`. The HEALTH INSURANCE (mutuelle) contracts
        taken out by the company: reference, population, option, contribution
        base and employer and employee rates, and the `affiliatedContractIds`
        (the affiliated employment contracts).
        """
        return self._get(self._company_path("/health-insurance-contracts"))

    def list_provident_fund_contracts(self) -> Any:
        """GET /companies/{companyId}/provident-fund-contracts — 🇫🇷.

        Scope `health-insurance:read` (the same as health insurance, it is not a
        typo). The company's PROVIDENT FUND contracts.
        """
        return self._get(self._company_path("/provident-fund-contracts"))
