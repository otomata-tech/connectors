"""Nextmotion — sales: quotes, invoices, payments and payment mediums, income
statistics, a patient's financial totals, and the product stock.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`).
"""
from __future__ import annotations

from typing import Any, Optional

from .._http import _id, _opt_id

_PERIOD_TYPES = ("year", "month", "week", "day")


class _SalesMixin:

    # ---- quotes & invoices ---------------------------------------------------

    def list_quotes(self, clinic_id: str, *, patient_id: Optional[str] = None,
                    limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/quotes (`patient` filter)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/quotes",
                          limit, offset, patient=_opt_id(patient_id, "patient_id"))

    def get_quote(self, quote_id: str) -> Any:
        """GET /v4/quotes/{quote_id}."""
        return self._get(f"/v4/quotes/{_id(quote_id, 'quote_id')}")

    def list_invoices(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/invoices (no filter documented)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/invoices",
                          limit, offset)

    def get_invoice(self, invoice_id: str) -> Any:
        """GET /v4/invoices/{invoice_id}."""
        return self._get(f"/v4/invoices/{_id(invoice_id, 'invoice_id')}")

    # ---- payments ------------------------------------------------------------

    def list_payments(self, clinic_id: str, *, invoice_id: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/payments — each payment embeds its invoice.

        Args:
            invoice_id: payments of that invoice (sent as `invoice`).
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/payments",
                          limit, offset, invoice=_opt_id(invoice_id, "invoice_id"))

    def get_payment(self, payment_id: str) -> Any:
        """GET /v4/payments/{payment_id}."""
        return self._get(f"/v4/payments/{_id(payment_id, 'payment_id')}")

    def list_payment_mediums(self, clinic_id: str, *, limit: int = 50,
                             offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/payment_mediums — custom payment means."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/payment_mediums",
                          limit, offset)

    def get_payment_medium(self, payment_medium_id: str) -> Any:
        """GET /v4/payment_mediums/{payment_medium_id}."""
        return self._get(
            f"/v4/payment_mediums/{_id(payment_medium_id, 'payment_medium_id')}")

    # ---- statistics ----------------------------------------------------------

    def _statistics(self, clinic_id: str, path: str, start_date: Optional[str],
                    end_date: Optional[str], period_type: Optional[str]) -> Any:
        if period_type is not None and period_type not in _PERIOD_TYPES:
            raise ValueError(f"period_type doit être l'un de {_PERIOD_TYPES} — "
                             f"reçu {period_type!r}.")
        return self._get(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/statistics/{path}",
                         start_time=start_date, end_time=end_date,
                         period_type=period_type)

    def get_appointment_income_statistics(self, clinic_id: str, *,
                                          start_date: Optional[str] = None,
                                          end_date: Optional[str] = None,
                                          period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/appointment_income — one chart.

        Args:
            start_date / end_date: `YYYY-MM-DD` (sent as `start_time`/`end_time`,
                end inclusive); omitted, the API spans 6 periods up to today.
            period_type: `year` | `month` (API default) | `week` | `day`.
        """
        return self._statistics(clinic_id, "appointment_income", start_date, end_date,
                                period_type)

    def list_treatment_type_statistics(self, clinic_id: str, *,
                                       start_date: Optional[str] = None,
                                       end_date: Optional[str] = None,
                                       period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/treatment_types — draft-quoted,
        quoted and invoiced treatment counts, one chart per treatment type."""
        return self._statistics(clinic_id, "treatment_types", start_date, end_date,
                                period_type)

    def list_treatment_type_income_statistics(self, clinic_id: str, *,
                                              start_date: Optional[str] = None,
                                              end_date: Optional[str] = None,
                                              period_type: Optional[str] = None) -> Any:
        """GET /v4/clinics/{clinic_id}/statistics/treatment_types/income — income
        from invoiced treatments, one chart per treatment type."""
        return self._statistics(clinic_id, "treatment_types/income", start_date,
                                end_date, period_type)

    def get_patient_stats(self, patient_id: str) -> Any:
        """GET /v4/patients/{patient_id}/stats — quoted, invoiced, paid,
        credit-note and reimbursement totals, first/last visit times, review
        requests, media counts. No identity in the response."""
        return self._get(f"/v4/patients/{_id(patient_id, 'patient_id')}/stats")

    # ---- product stock (read only) -------------------------------------------

    def list_products(self, clinic_id: str, *, search: Optional[str] = None,
                      stock_state: Optional[str] = None,
                      expiring_within_days: Optional[int] = None,
                      order: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/products — the clinic's stock, one row per lot.

        A product row embeds its catalogue entry (`global_product`: name, brand).

        Args:
            search: free-text search.
            stock_state: `low` | `out` | `ok`.
            expiring_within_days: >= 1 — lots expiring within that many days.
            order: `name`, `brand_name` (API default), `stock_level` or
                `warning_level`, each optionally prefixed by `-`.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/products",
                          limit, offset, search=search, stock_state=stock_state,
                          expiring_within_days=expiring_within_days, order=order)

    def get_product(self, product_id: str) -> Any:
        """GET /v4/products/{product_id}."""
        return self._get(f"/v4/products/{_id(product_id, 'product_id')}")
