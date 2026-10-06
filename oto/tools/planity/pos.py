"""The till: periods, receipts, lines, payments.

A **period** is a till session (opening → closing); it carries its receipts
inline, under `receipts`. A salon has hundreds of them: every read goes through
a range on `createdAt`, never through the whole node.

⚠️ **A receipt carries a COMPLETE snapshot of the customer** — name, phone,
email, address, comment — frozen at the moment of payment. This module is a
library and returns what Planity gives. Projection is decided at a tool's
boundary, and there it is reduced to the identifier: no name, no contact, no
address. The backend connector carries that rule and its reason."""
from __future__ import annotations

from typing import Optional

from .firebase_ws import FirebaseRTDB, range_on

NOEUD_PERIODES = "pos_periods"
NOEUD_MOYENS_PAIEMENT = "business_payment_methods"

#: The sort index of till periods. Milliseconds, as everywhere at Planity.
INDEX_PERIODE = "createdAt"


def _ligne(brut: dict) -> dict:
    """A receipt line. `title` names the SERVICE or the PRODUCT, not the customer."""
    return {
        "title": brut.get("title"),
        "price_cents": brut.get("price"),
        "unit_price_cents": brut.get("unitPrice"),
        "quantity": brut.get("quantity"),
        "service_id": brut.get("serviceId"),
        "product_id": brut.get("productId"),
        "seller_id": brut.get("seller"),
        "duration_minutes": brut.get("duration"),
        "vat_code": brut.get("vatCode"),
        "vat_rate": brut.get("vatRate"),
        "vat_excluded_cents": brut.get("vatExcluded"),
        "from_appointment": brut.get("comingFromAppointment"),
    }


def _paiement(brut: dict) -> dict:
    return {
        "method": brut.get("method"),
        "amount_cents": brut.get("amount"),
        "tip_cents": brut.get("tpeTip"),
    }


def ticket(receipt_id: str, brut: dict) -> dict:
    """A named receipt. `customer` is left as is — see the module warning."""
    lignes = brut.get("lines") or {}
    paiements = brut.get("paymentMethods") or {}
    rdv = brut.get("appointment") or {}
    client = brut.get("customer")
    return {
        "id": receipt_id,
        "number": brut.get("number"),
        "sequence": brut.get("sequence"),
        "created_at": brut.get("createdAt"),
        "operation_type": brut.get("operationType"),
        "lines_count": brut.get("linesCount"),
        "lines": [_ligne(l) for l in _valeurs(lignes) if isinstance(l, dict)],
        "payments": [_paiement(p) for p in _valeurs(paiements) if isinstance(p, dict)],
        "discount_total_cents": brut.get("discountTotal"),
        "vat_included_total_cents": brut.get("vatIncludedTotal"),
        "vat_excluded_total_cents": brut.get("vatExcludedTotal"),
        "vat_total_cents": brut.get("vatTotal"),
        "vat_rates": brut.get("vatRates"),
        "seller_id": brut.get("userId"),
        "seller_name": brut.get("userName"),
        # A cancelled receipt carries `status`; a normal receipt has no such
        # field at all. Returning `None` would make "no status" read as "unknown
        # cancelled" — it is `"OK"` by ABSENCE, which is what `cancelled` says.
        "status": brut.get("status"),
        "cancelled": brut.get("status") == "CANCELLED",
        "cancelled_at": brut.get("cancelledAt"),
        "cancelled_by": brut.get("cancelledBy"),
        # `appointment` is a MAP {veventId: {...}}: the key carries the link to
        # the calendar, not a value inside it.
        "appointment_ids": sorted(rdv.keys()) if isinstance(rdv, dict) else [],
        "customer_id": client.get("id") if isinstance(client, dict) else None,
        "customer": client,
        "raw": brut,
    }


def _valeurs(noeud) -> list:
    """Planity writes its collections sometimes as a map, sometimes as a list."""
    if isinstance(noeud, dict):
        return list(noeud.values())
    if isinstance(noeud, list):
        return noeud
    return []


def periode(period_id: str, brut: dict, avec_tickets: bool = True) -> dict:
    tickets = brut.get("receipts") or {}
    sortie = {
        "id": period_id,
        "created_at": brut.get("createdAt"),
        "closed_at": brut.get("closedAt"),
        "open": brut.get("closedAt") is None,
        "initial_amount_cents": brut.get("initialAmount"),
        "final_amount_cents": brut.get("finalAmount"),
        "sequences": brut.get("sequences"),
        "receipts_count": len(tickets) if isinstance(tickets, dict) else 0,
    }
    if avec_tickets and isinstance(tickets, dict):
        sortie["receipts"] = [ticket(rid, r) for rid, r in tickets.items()
                              if isinstance(r, dict)]
    return sortie


async def lire_periodes(db: FirebaseRTDB, business_id: str, gte_ms: int, lte_ms: int,
                        limite: Optional[int] = None) -> list[dict]:
    """The till periods opened in the window, WITHOUT their receipts.

    Deliberately without receipts: a period carries dozens, each with a customer
    snapshot. A period list that embedded them would move thousands of records
    just to answer "how many sessions this month"."""
    brut = await db.get(f"{NOEUD_PERIODES}/{business_id}",
                        range_on(INDEX_PERIODE, gte_ms, lte_ms, limit=limite))
    if not isinstance(brut, dict):
        return []
    periodes = [periode(pid, p, avec_tickets=False)
                for pid, p in brut.items() if isinstance(p, dict)]
    periodes.sort(key=lambda p: p.get("created_at") or 0)
    return periodes


async def lire_periode(db: FirebaseRTDB, business_id: str, period_id: str) -> Optional[dict]:
    brut = await db.get(f"{NOEUD_PERIODES}/{business_id}/{period_id}")
    if not isinstance(brut, dict) or not brut:
        return None
    return periode(period_id, brut, avec_tickets=True)


async def lire_ticket(db: FirebaseRTDB, business_id: str, period_id: str,
                      receipt_id: str) -> Optional[dict]:
    """A specific receipt. It lives UNDER its period — it has no address of its own."""
    brut = await db.get(
        f"{NOEUD_PERIODES}/{business_id}/{period_id}/receipts/{receipt_id}")
    # Empty = absent. A skeleton receipt would read as a zero-euro receipt.
    if not isinstance(brut, dict) or not brut:
        return None
    return ticket(receipt_id, brut)


async def lire_moyens_paiement(db: FirebaseRTDB, business_id: str) -> list[dict]:
    """The salon's payment methods — the table that gives `method` its name."""
    brut = await db.get(f"{NOEUD_MOYENS_PAIEMENT}/{business_id}")
    if not isinstance(brut, dict):
        return []
    return [{"id": mid, "name": m.get("name"), "color": m.get("color"),
             "sort": m.get("sort")}
            for mid, m in brut.items() if isinstance(m, dict)]
