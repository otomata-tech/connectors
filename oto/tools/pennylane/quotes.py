"""Devis Pennylane (`quotes`) — créer, lire, lister, changer de statut, facturer.

Troisième module du client (cf. `ledger.py` pour le même découpage) :
`PennylaneClient` en hérite et lui fournit le transport (`fetch`,
`fetch_all_pages`, `post`, `put`).

**Un devis n'a pas de brouillon.** Contrairement à une facture (`draft`), un
devis naît au statut `pending` (en attente d'acceptation) ; il n'est envoyé au
client que par un geste explicite. Ses statuts : `pending`, `accepted`,
`denied`, `invoiced`, `expired` — posés par `update_quote_status`.

**Les lignes ont le schéma des factures** (`invoice_lines`, `oneOf` produit ou
ligne libre, `vat_rate` en code Pennylane) : ce module ne les réécrit pas.

**Le PDF** : `public_file_url` du devis, lien qui expire (30 minutes d'après la
référence) — le relire juste avant de s'en servir.

Scopes : `quotes:readonly` pour lire, `quotes:all` pour écrire ; facturer un
devis (`create_invoice_from_quote`) crée une facture et demande
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
        """`POST /quotes` — crée un devis (statut `pending`).

        lines: même schéma que `create_customer_invoice` (produit ou ligne libre).
        deadline: fin de validité du devis.
        pdf_free_text: texte libre imprimé sur le PDF (champ API
               `pdf_invoice_free_text`).
        quote_template_id: modèle de rendu du devis.
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
        """`GET /quotes` — paginé par curseur, filtres serveur optionnels
        (`status`, `customer_id`)."""
        filtres = []
        if status:
            if status not in QUOTE_STATUSES:
                raise ValueError(f"statut de devis inconnu : {status!r} "
                                 f"(attendu : {', '.join(QUOTE_STATUSES)})")
            filtres.append({"field": "status", "operator": "eq", "value": status})
        if customer_id is not None:
            filtres.append({"field": "customer_id", "operator": "eq",
                            "value": str(customer_id)})
        params = {"filter": json.dumps(filtres)} if filtres else None
        return self.fetch_all_pages("quotes", params=params, max_pages=max_pages)

    def get_quote(self, quote_id: int) -> dict:
        """`GET /quotes/{id}` — le devis, dont `public_file_url` (PDF, lien qui
        expire) et `status`."""
        return self.fetch(f"quotes/{int(quote_id)}")

    def get_quote_lines(self, quote_id: int) -> list:
        """`GET /quotes/{id}/invoice_lines` — les lignes du devis."""
        data = self.fetch(f"quotes/{int(quote_id)}/invoice_lines")
        return data.get("items", []) if isinstance(data, dict) else []

    def update_quote_status(self, quote_id: int, status: str) -> dict:
        """`PUT /quotes/{id}/update_status` — `pending`, `accepted`, `denied`,
        `invoiced` ou `expired`."""
        if status not in QUOTE_STATUSES:
            raise ValueError(f"statut de devis inconnu : {status!r} "
                             f"(attendu : {', '.join(QUOTE_STATUSES)})")
        return self.put(f"quotes/{int(quote_id)}/update_status", {"status": status})

    def create_invoice_from_quote(self, quote_id: int, draft: bool = True,
                                  external_reference: str = None,
                                  customer_invoice_template_id: int = None) -> dict:
        """`POST /customer_invoices/create_from_quote` — une facture client qui
        reprend le devis (client, lignes…). Brouillon par défaut."""
        body = {"quote_id": int(quote_id), "draft": draft}
        if external_reference:
            body["external_reference"] = external_reference
        if customer_invoice_template_id:
            body["customer_invoice_template_id"] = customer_invoice_template_id
        return self.post("customer_invoices/create_from_quote", body)
