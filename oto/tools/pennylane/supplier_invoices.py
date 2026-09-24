"""Factures d'ACHAT Pennylane (`supplier_invoices`) — importer, lire, corriger, valider.

Quatrième module du client (même découpage que `ledger.py` et `quotes.py`) :
`PennylaneClient` en hérite et lui fournit le transport (`fetch`,
`fetch_all_pages`, `post`, `put`, `_appel`).

Cycle d'une facture d'achat importée par l'API : `import_supplier_invoice` la
crée à partir d'une pièce déjà déposée ; avec `import_as_incomplete=True` elle
arrive en `accounting_status = validation_needed`. On la corrige au besoin
(`update_supplier_invoice` : libellé, dates, montants, et les LIGNES — dont leur
`vat_rate`), puis on la valide (`validate_supplier_invoice_accounting`), ce qui
la passe en `complete`. `accounting_status` ∈ `draft`, `archived`, `entry`,
`validation_needed`, `complete`.

**Montants** : `currency_amount_before_tax` (HT) n'existe qu'au niveau FACTURE ;
une LIGNE ne porte que `currency_amount` (TTC, taxes et remises comprises) et
`currency_tax` — la référence ne connaît pas de HT par ligne.

**`vat_rate` d'autoliquidation** (enum des lignes, référence v2) : `intracom_21`,
`intracom_55`, `intracom_85`, `intracom_100`, `extracom`, `crossborder`,
`FR_85_construction`, `FR_100_construction`, `FR_200_construction`, et
`exempt`. La référence ne définit pas ces codes au-delà de leur nom, et
n'expose AUCUN `intracom_200` : le choix du code d'une acquisition intra-UE à
20 % est une question comptable, pas une valeur à déduire de ce module.

Scopes : `supplier_invoices:readonly` pour lire ; `supplier_invoices:all` pour
importer, corriger et valider.
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
        """Crée une facture FOURNISSEUR à partir d'une pièce déjà uploadée.

        `POST /supplier_invoices/import` : lie le `file_attachment_id` (cf.
        `upload_file_bytes`) à une facture fournisseur en brouillon. Pas d'OCR côté
        Pennylane — l'appelant FOURNIT les champs (lus depuis le PDF) : `supplier_id`,
        `date`/`deadline` (ISO), montants **en string** (`currency_amount_before_tax`,
        `currency_amount`=TTC, `currency_tax`), et `invoice_lines` (≥1). Pennylane
        déduplique par PDF (422 si le même file_attachment est ré-importé) ;
        `external_reference` trace la source et garde l'appelant idempotent.
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
        """`GET /supplier_invoices/{id}` — la facture, dont `accounting_status`.
        Ses lignes sont une sous-ressource (`get_supplier_invoice_lines`)."""
        return self.fetch(f"supplier_invoices/{int(invoice_id)}")

    def get_supplier_invoice_lines(self, invoice_id: int,
                                   max_pages: Optional[int] = None) -> list:
        """`GET /supplier_invoices/{id}/invoice_lines` — les lignes, avec leur `id`
        (à passer à `update_supplier_invoice`) et leur `vat_rate`. Paginé par
        curseur (20 par défaut côté API) : on suit toutes les pages."""
        return self.fetch_all_pages(f"supplier_invoices/{int(invoice_id)}/invoice_lines",
                                    max_pages=max_pages)

    def update_supplier_invoice(self, invoice_id: int, fields: Optional[dict] = None,
                                invoice_lines: Optional[dict] = None) -> dict:
        """`PUT /supplier_invoices/{id}` — corrige une facture d'achat.

        fields: champs de niveau facture à changer (`label`, `date`, `deadline`,
            `invoice_number`, `supplier_id`, `currency_amount_before_tax`,
            `currency_amount`, `currency_tax`, `external_reference`…).
        invoice_lines: `{"update": [{"id": …, "vat_rate": …}, …], "create": [...],
            "delete": [{"id": …}]}` — `update` et `delete` exigent l'`id` de la
            ligne (`get_supplier_invoice_lines`) ; `create` exige `label`,
            `currency_amount`, `currency_tax`, `vat_rate`.
        La référence ne documente pas de refus sur une facture déjà `complete` :
        c'est l'API qui tranche (un refus remonte en exception).
        """
        body = dict(fields or {})
        if invoice_lines is not None:
            if not isinstance(invoice_lines, dict) or not invoice_lines:
                raise ValueError("invoice_lines : objet {create|update|delete: [...]} attendu")
            inconnus = set(invoice_lines) - set(LINE_OPS)
            if inconnus:
                raise ValueError(f"invoice_lines : clé(s) inconnue(s) {sorted(inconnus)} "
                                 f"(attendu : {', '.join(LINE_OPS)})")
            for cle in ("update", "delete"):
                for ligne in invoice_lines.get(cle) or []:
                    if not isinstance(ligne, dict) or ligne.get("id") is None:
                        raise ValueError(f"invoice_lines.{cle} : chaque ligne exige son `id`")
            body["invoice_lines"] = invoice_lines
        if not body:
            raise ValueError("rien à modifier : ni champ, ni ligne")
        return self.put(f"supplier_invoices/{int(invoice_id)}", body)

    def validate_supplier_invoice_accounting(self, invoice_id: int) -> dict:
        """`PUT /supplier_invoices/{id}/validate_accounting` — passe la facture en
        `complete` (écriture comptable validée). Sans corps. Geste ENGAGEANT : à ne
        jouer que sur demande explicite ; l'API n'expose pas le geste inverse.
        422 si les lignes d'écriture ne sont pas équilibrées."""
        return self._appel("PUT", f"supplier_invoices/{int(invoice_id)}/validate_accounting")
