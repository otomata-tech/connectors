"""Pennylane SUPPLIER invoices (`supplier_invoices`) — import, read, correct, validate.

Fourth module of the client (same split as `ledger.py` and `quotes.py`):
`PennylaneClient` inherits it and provides the transport (`fetch`,
`fetch_all_pages`, `post`, `put`, `_appel`).

Lifecycle of a supplier invoice imported through the API: `import_supplier_invoice` creates
it from an already uploaded document; with `import_as_incomplete=True` it
arrives as `accounting_status = validation_needed`. It is corrected as needed
(`update_supplier_invoice`: label, dates, amounts, and the LINES — including their
`vat_rate`), then validated (`validate_supplier_invoice_accounting`), which moves
it to `complete`. `accounting_status` ∈ `draft`, `archived`, `entry`,
`validation_needed`, `complete`.

**Amounts**: `currency_amount_before_tax` (excl. VAT) only exists at INVOICE level;
a LINE only carries `currency_amount` (incl. VAT, taxes and discounts included) and
`currency_tax` — the reference knows no per-line excl.-VAT amount.

**Reverse-charge `vat_rate`** (line enum, v2 reference): `intracom_21`,
`intracom_55`, `intracom_85`, `intracom_100`, `extracom`, `crossborder`,
`FR_85_construction`, `FR_100_construction`, `FR_200_construction`, and
`exempt`. The reference does not define these codes beyond their name, and
exposes NO `intracom_200`: choosing the code for an intra-EU acquisition at
20% is an accounting question, not a value to deduce from this module.

Scopes: `supplier_invoices:readonly` to read; `supplier_invoices:all` to
import, correct and validate.
"""
from __future__ import annotations

from typing import Optional

LINE_OPS = ("create", "update", "delete")


class SupplierInvoicesMixin:

    def import_supplier_invoice(
        self, file_attachment_id: int, supplier_id: int, date: str, deadline: str,
        currency_amount_before_tax: str, currency_amount: str, currency_tax: str,
        invoice_lines: list[dict], currency: str = "EUR",
        external_reference: Optional[str] = None, import_as_incomplete: bool = False,
        invoice_number: Optional[str] = None, label: Optional[str] = None,
    ) -> dict:
        """Create a SUPPLIER invoice from an already uploaded document.

        `POST /supplier_invoices/import`: links the `file_attachment_id` (see
        `upload_file_bytes`) to a draft supplier invoice. No OCR on the
        Pennylane side — the caller SUPPLIES the fields (read from the PDF): `supplier_id`,
        `date`/`deadline` (ISO), amounts **as strings** (`currency_amount_before_tax`,
        `currency_amount`=incl. VAT, `currency_tax`), and `invoice_lines` (≥1). Pennylane
        deduplicates by PDF (422 if the same file_attachment is re-imported);
        `external_reference` traces the source and keeps the caller idempotent.
        """
        body = {
            "file_attachment_id": file_attachment_id,
            "supplier_id": supplier_id,
            "date": date,
            "deadline": deadline,
            "currency": currency,
            "currency_amount_before_tax": currency_amount_before_tax,
            "currency_amount": currency_amount,
            "currency_tax": currency_tax,
            "invoice_lines": invoice_lines,
            "import_as_incomplete": import_as_incomplete,
        }
        if external_reference:
            body["external_reference"] = external_reference
        if invoice_number:
            body["invoice_number"] = invoice_number
        if label:
            body["label"] = label
        return self.post("supplier_invoices/import", body)

    def get_supplier_invoice(self, invoice_id: int) -> dict:
        """`GET /supplier_invoices/{id}` — the invoice, including `accounting_status`.
        Its lines are a sub-resource (`get_supplier_invoice_lines`)."""
        return self.fetch(f"supplier_invoices/{int(invoice_id)}")

    def get_supplier_invoice_lines(self, invoice_id: int,
                                   max_pages: Optional[int] = None) -> list:
        """`GET /supplier_invoices/{id}/invoice_lines` — the lines, with their `id`
        (to pass to `update_supplier_invoice`) and their `vat_rate`. Cursor-paginated
        (20 by default on the API side): all pages are followed."""
        return self.fetch_all_pages(f"supplier_invoices/{int(invoice_id)}/invoice_lines",
                                    max_pages=max_pages)

    def update_supplier_invoice(self, invoice_id: int, fields: Optional[dict] = None,
                                invoice_lines: Optional[dict] = None) -> dict:
        """`PUT /supplier_invoices/{id}` — corrects a supplier invoice.

        fields: invoice-level fields to change (`label`, `date`, `deadline`,
            `invoice_number`, `supplier_id`, `currency_amount_before_tax`,
            `currency_amount`, `currency_tax`, `external_reference`…).
        invoice_lines: `{"update": [{"id": …, "vat_rate": …}, …], "create": [...],
            "delete": [{"id": …}]}` — `update` and `delete` require the line's
            `id` (`get_supplier_invoice_lines`); `create` requires `label`,
            `currency_amount`, `currency_tax`, `vat_rate`.
        The reference does not document a refusal on an already `complete` invoice:
        the API decides (a refusal surfaces as an exception).
        """
        body = dict(fields or {})
        if invoice_lines is not None:
            if not isinstance(invoice_lines, dict) or not invoice_lines:
                raise ValueError("invoice_lines: expected object {create|update|delete: [...]}")
            inconnus = set(invoice_lines) - set(LINE_OPS)
            if inconnus:
                raise ValueError(f"invoice_lines: unknown key(s) {sorted(inconnus)} "
                                 f"(expected: {', '.join(LINE_OPS)})")
            for cle in ("update", "delete"):
                for ligne in invoice_lines.get(cle) or []:
                    if not isinstance(ligne, dict) or ligne.get("id") is None:
                        raise ValueError(f"invoice_lines.{cle}: each line requires its `id`")
            body["invoice_lines"] = invoice_lines
        if not body:
            raise ValueError("nothing to change: neither field nor line")
        return self.put(f"supplier_invoices/{int(invoice_id)}", body)

    def validate_supplier_invoice_accounting(self, invoice_id: int) -> dict:
        """`PUT /supplier_invoices/{id}/validate_accounting` — moves the invoice to
        `complete` (accounting entry validated). No body. COMMITTING action: only to be
        run on explicit request; the API does not expose the reverse action.
        422 if the entry lines are not balanced."""
        return self._appel("PUT", f"supplier_invoices/{int(invoice_id)}/validate_accounting")
