"""Pennylane general ledger — entries, journals, line lettering.

Second module of the client (see `brevo` for the same split): `PennylaneClient`
inherits it and provides the transport (`fetch`, `fetch_all_pages`, `post`,
`delete`). Kept separate because the general ledger is a domain of its own, and
`client.py` had already outgrown the size at which a file can still be read.

**Three distinct scopes, not one.** Pennylane split the old `ledger` scope:
reading journals requires `journals:*`, reading the chart of accounts
`ledger_accounts:*`, reading or writing entries `ledger_entries:*`. A key
that reads entries therefore does not necessarily read journals — and the
scope is specific to whoever created the key. The effective rights can be read on `GET /me`,
`scopes` field.

**Lettering here is not bank reconciliation.** The word "lettering"
covers two actions: associating a bank transaction with an invoice
(`match_transaction`, earlier in the client), and associating general-ledger
lines with each other (here). Different objects, different endpoints.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Optional


def _somme(lignes: list[dict], champ: str) -> Decimal:
    total = Decimal(0)
    for ligne in lignes:
        brut = str(ligne.get(champ) or "0").strip() or "0"
        try:
            total += Decimal(brut)
        except InvalidOperation:
            raise ValueError(
                f"Unreadable amount in `{champ}`: {brut!r}. Pennylane expects a "
                "decimal string (e.g. \"120.50\"), not a number or an expression.")
    return total


class LedgerMixin:
    """The general-ledger actions. Expects the transport of `PennylaneClient`."""

    # --- read --------------------------------------------------------------

    @staticmethod
    def filtre(clauses: list[dict]) -> str:
        """Encode clauses in the format Pennylane expects in the query.

        The `filter` parameter is a JSON STRING, not an object: a list of
        `{"field": …, "operator": …, "value": …}`. Example given by the docs:
        `[{"field": "date", "operator": "gteq", "value": "2026-01-01"}]`.

        Filterable fields on entries: `id`, `date`, `journal_id`.
        Operators: `lt`, `lteq`, `gt`, `gteq`, `eq`, `not_eq`, plus `in` and
        `not_in` on `id` and `journal_id`.
        """
        return json.dumps(clauses, separators=(",", ":"))

    def get_journals(self, max_pages: Optional[int] = None) -> list:
        """The company's journals — `{id, code, label, type}`.

        Prerequisite of `create_ledger_entry`, which requires a `journal_id`. These ids
        are **specific to the company**: resolve them every time, never
        hard-code them. Scope `journals:readonly` or `journals:all`.
        """
        return self.fetch_all_pages("journals", max_pages=max_pages)

    def get_ledger_accounts(self) -> list:
        """The chart of accounts — the `ledger_account_id`s that every entry
        line requires. Scope `ledger_accounts:readonly` or `ledger_accounts:all`."""
        return self.fetch_all_pages("ledger_accounts")

    def get_ledger_entries(self, max_pages: Optional[int] = None,
                           clauses: Optional[list[dict]] = None) -> list:
        """The general-ledger entries.

        ⚠️ Without `clauses`, the WHOLE history comes back — on a real
        accounting, thousands of entries. Filter at the source: `clauses` is
        a list of `{"field", "operator", "value"}` (see `filtre`), the only
        way to find an entry without paginating through everything.
        """
        params = {"filter": self.filtre(clauses)} if clauses else None
        return self.fetch_all_pages("ledger_entries", params=params,
                                    max_pages=max_pages)

    def get_ledger_entry(self, entry_id: int) -> dict:
        """ONE entry by its id — re-reading what was just posted.

        Scope `ledger_entries:readonly` or `ledger_entries:all`.
        """
        return self.fetch(f"ledger_entries/{entry_id}")

    def get_ledger_entry_lines(self, entry_id: int,
                               max_pages: Optional[int] = None) -> list:
        """The lines of an entry, with their `id` — what lettering consumes."""
        return self.fetch_all_pages(f"ledger_entries/{entry_id}/ledger_entry_lines",
                                    max_pages=max_pages)

    def get_lettered_lines(self, line_id: int,
                           max_pages: Optional[int] = None) -> list:
        """The lines lettered WITH a given line.

        The only way to see what a lettering actually pulled in: the
        action is absorbing (see `letter_ledger_entry_lines`), so its result
        is not always what was asked for.
        """
        return self.fetch_all_pages(
            f"ledger_entry_lines/{line_id}/lettered_ledger_entry_lines",
            max_pages=max_pages)

    # --- write -------------------------------------------------------------

    @staticmethod
    def controler_ecriture(ledger_entry_lines: list[dict]) -> dict:
        """Check an entry WITHOUT writing it, and return its summary.

        Separate from `create_ledger_entry` so that a caller can show a
        human what will be posted, with its totals, BEFORE posting it — the action
        having no draft at Pennylane. Both paths therefore share
        the same rule: what is validated here is exactly what will be sent.

        Raises on refusal, with enough to correct. Otherwise returns `{lignes, total_debit,
        total_credit}`.
        """
        if not ledger_entry_lines:
            raise ValueError("An accounting entry requires at least one line.")
        if len(ledger_entry_lines) > 1000:
            raise ValueError(
                f"{len(ledger_entry_lines)} lines: Pennylane accepts at most 1000 "
                "per request. Split the entry.")
        debits, credits = _somme(ledger_entry_lines, "debit"), _somme(
            ledger_entry_lines, "credit")
        if debits != credits:
            raise ValueError(
                f"Unbalanced entry: {debits} debit against {credits} "
                f"credit, gap of {debits - credits}. Pennylane would refuse it; "
                "correct the lines before calling again.")
        for i, ligne in enumerate(ledger_entry_lines):
            if not ligne.get("ledger_account_id"):
                raise ValueError(
                    f"Line {i} without `ledger_account_id`: the chart-of-accounts "
                    "account is mandatory (see `get_ledger_accounts`).")
        return {"lignes": len(ledger_entry_lines), "total_debit": str(debits),
                "total_credit": str(credits)}

    def create_ledger_entry(self, date: str, label: str, journal_id: int,
                            ledger_entry_lines: list[dict],
                            due_date: Optional[str] = None,
                            currency: Optional[str] = None,
                            piece_number: Optional[str] = None) -> dict:
        """Create a general-ledger entry — `POST /ledger_entries`.

        Scope `ledger_entries:all`. Each line carries `debit`, `credit` (decimal
        **strings**) and `ledger_account_id`; `label` is optional.

        ⚠️ **Pennylane has no draft for an accounting entry.** The
        rest of the connector is draft-first (an invoice is created as a
        draft, then finalized); here the entry is posted immediately. The caller must therefore announce the exact detail BEFORE calling, and
        know that undoing it goes through `PUT /ledger_entries/{id}`, not through a
        deletion.

        ⚠️ **The order of the returned lines is not guaranteed**: to find
        a line's id, match on its content, never on its position.

        The balance is checked HERE rather than left to Pennylane's 422:
        the figure of the gap is what makes correction possible, "not balanced" is not.
        """
        self.controler_ecriture(ledger_entry_lines)
        body = {"date": date, "label": label, "journal_id": journal_id,
                "ledger_entry_lines": ledger_entry_lines}
        if due_date:
            body["due_date"] = due_date
        if currency:
            body["currency"] = currency
        if piece_number:
            body["piece_number"] = piece_number
        return self.post("ledger_entries", body)

    def update_ledger_entry(self, entry_id: int, **fields) -> dict:
        """Modify a posted entry — `PUT /ledger_entries/{id}`.

        The only recourse when an entry is wrong: there is no
        entry deletion in the API.

        ⚠️ **This action can DESTROY lines.** `ledger_entry_lines` takes
        three sub-objects there — `create`, `update`, `delete` — and `delete`
        removes lines by id. Correcting is therefore no more harmless than
        creating: the caller must submit the detail to a human in the same
        way.
        """
        return self.put(f"ledger_entries/{entry_id}", fields)

    # --- line lettering ----------------------------------------------------

    def letter_ledger_entry_lines(self, line_ids: list[int],
                                  unbalanced_lettering_strategy: str = "none") -> dict:
        """Letter general-ledger lines with each other.

        `POST /ledger_entry_lines/lettering` — note the path: the docs slug
        says "letter", the OpenAPI serves `lettering`. Scope `ledger_entries:all`.

        ⚠️ **The action is absorbing**: if a line passed is already lettered, the
        lettering extends to its already associated lines. Asking for [A, C] when A and
        B are lettered returns [A, B, C]. A caller that ignores this widens a
        lettering unintentionally — hence `get_lettered_lines` to check.

        `unbalanced_lettering_strategy`: `"none"` refuses an unbalanced
        lettering, `"partial"` accepts it. The default refuses, because an unintended
        imbalance is more costly than a rejected call.
        """
        return self.post("ledger_entry_lines/lettering",
                         self._corps_lettrage(line_ids, unbalanced_lettering_strategy))

    def unletter_ledger_entry_lines(self, line_ids: list[int],
                                    unbalanced_lettering_strategy: str = "none") -> dict:
        """Undo a lettering — `DELETE /ledger_entry_lines/lettering`, same path.

        This is what makes lettering reversible, and therefore safe to expose.
        """
        return self.delete("ledger_entry_lines/lettering",
                           self._corps_lettrage(line_ids,
                                                unbalanced_lettering_strategy))

    @staticmethod
    def _corps_lettrage(line_ids: list[int], strategie: str) -> dict:
        if strategie not in ("none", "partial"):
            raise ValueError(
                f"`unbalanced_lettering_strategy` is {strategie!r}: Pennylane "
                "only accepts 'none' (refuses an unbalanced lettering) or "
                "'partial' (accepts it).")
        if len(line_ids) < 2:
            raise ValueError(
                f"{len(line_ids)} line(s): lettering associates at least two.")
        return {"unbalanced_lettering_strategy": strategie,
                "ledger_entry_lines": [{"id": int(i)} for i in line_ids]}
