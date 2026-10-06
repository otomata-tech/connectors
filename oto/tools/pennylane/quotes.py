"""Pennylane quotes (`quotes`) — create, read, list, change status, invoice.

Third module of the client (see `ledger.py` for the same split):
`PennylaneClient` inherits it and provides the transport (`fetch`,
`fetch_all_pages`, `post`, `put`).

**A quote has no draft.** Unlike an invoice (`draft`), a
quote is born with status `pending` (awaiting acceptance); it is only sent to the
customer by an explicit action. Its statuses: `pending`, `accepted`,
`denied`, `invoiced`, `expired` — set by `update_quote_status`.

**The lines have the invoice schema** (`invoice_lines`, `oneOf` product or
free line, `vat_rate` as a Pennylane code): this module does not rewrite them.

**The PDF**: the quote's `public_file_url`, a link that expires (30 minutes according to
the reference) — re-read it just before using it.

Scopes: `quotes:readonly` to read, `quotes:all` to write; invoicing a
quote (`create_invoice_from_quote`) creates an invoice and requires
`customer_invoices:all`.
"""
from __future__ import annotations

import json
from typing import Optional

QUOTE_STATUSES = ("pending", "accepted", "denied", "invoiced", "expired")


class QuotesMixin:

    def create_quote(self, customer_id: int, date: str, deadline: str,
                     lines: list[dict], external_reference: str = None,
                     currency: str = "EUR", language: str = "fr_FR",
                     pdf_free_text: str = None,
                     quote_template_id: int = None) -> dict:
        """`POST /quotes` — creates a quote (status `pending`).

        lines: same schema as `create_customer_invoice` (product or free line).
        deadline: end of the quote's validity.
        pdf_free_text: free text printed on the PDF (API field
               `pdf_invoice_free_text`).
        quote_template_id: rendering template of the quote.
        """
        body = {
            "customer_id": customer_id,
            "date": date,
            "deadline": deadline,
            "currency": currency,
            "language": language,
            "invoice_lines": lines,
        }
        if external_reference:
            body["external_reference"] = external_reference
        if pdf_free_text:
            body["pdf_invoice_free_text"] = pdf_free_text
        if quote_template_id:
            body["quote_template_id"] = quote_template_id
        return self.post("quotes", body)

    def list_quotes(self, max_pages: Optional[int] = None,
                    status: Optional[str] = None,
                    customer_id: Optional[int] = None) -> list:
        """`GET /quotes` — cursor-paginated, optional server-side filters
        (`status`, `customer_id`)."""
        filtres = []
        if status:
            if status not in QUOTE_STATUSES:
                raise ValueError(f"unknown quote status: {status!r} "
                                 f"(expected: {', '.join(QUOTE_STATUSES)})")
            filtres.append({"field": "status", "operator": "eq", "value": status})
        if customer_id is not None:
            filtres.append({"field": "customer_id", "operator": "eq",
                            "value": str(customer_id)})
        params = {"filter": json.dumps(filtres)} if filtres else None
        return self.fetch_all_pages("quotes", params=params, max_pages=max_pages)

    def get_quote(self, quote_id: int) -> dict:
        """`GET /quotes/{id}` — the quote, including `public_file_url` (PDF, expiring
        link) and `status`."""
        return self.fetch(f"quotes/{int(quote_id)}")

    def get_quote_lines(self, quote_id: int) -> list:
        """`GET /quotes/{id}/invoice_lines` — the quote's lines."""
        data = self.fetch(f"quotes/{int(quote_id)}/invoice_lines")
        return data.get("items", []) if isinstance(data, dict) else []

    def update_quote_status(self, quote_id: int, status: str) -> dict:
        """`PUT /quotes/{id}/update_status` — `pending`, `accepted`, `denied`,
        `invoiced` or `expired`."""
        if status not in QUOTE_STATUSES:
            raise ValueError(f"unknown quote status: {status!r} "
                             f"(expected: {', '.join(QUOTE_STATUSES)})")
        return self.put(f"quotes/{int(quote_id)}/update_status", {"status": status})

    def create_invoice_from_quote(self, quote_id: int, draft: bool = True,
                                  external_reference: str = None,
                                  customer_invoice_template_id: int = None) -> dict:
        """`POST /customer_invoices/create_from_quote` — a customer invoice that
        takes over the quote (customer, lines…). Draft by default."""
        body = {"quote_id": int(quote_id), "draft": draft}
        if external_reference:
            body["external_reference"] = external_reference
        if customer_invoice_template_id:
            body["customer_invoice_template_id"] = customer_invoice_template_id
        return self.post("customer_invoices/create_from_quote", body)
