"""
GoCardless API Pro Client — READ-ONLY surface for direct-debit data.

Why read-only: in oto's uses (reconciliation, handling of failed
direct debits → credit notes), GoCardless is only a **read source**.
The mutation (issuing a credit note) lives elsewhere (Pennylane). We therefore expose
no POST/PUT/DELETE here — an agent cannot cancel a direct debit by
mistake.

Auth: Bearer token. `GoCardless-Version` header mandatory.
Key always supplied by the consumer (`api_key`, required).
⚠️ A `live_` token hits real data.

Data chain: payment → links.mandate → mandate.links.customer.
The reason for a failure lives in the Events API (action=failed).
A bank payout (payout, PO…) groups lines (payout_items): one
per paid-out payment, failure, chargeback, refund or fee, each
linked to its payment (links.payment).

Usage:
    client = GoCardlessClient(api_key="live_...")
    failed = client.list_payments(status="failed", limit=20)
    party = client.payment_party(failed[0]["id"])   # customer + resolved reason
"""

import re
import time
from typing import Optional

import requests

from ..common.credentials import require
from ..common.errors import UpstreamHTTPError


def _to_rfc3339(value: Optional[str]) -> Optional[str]:
    """Normalizes a date for the GoCardless `created_at[*]` filters.

    The API requires an RFC3339 date-time — a bare date (`2026-05-25`) returns
    a 422 "not a valid date-time". We complete it with the start of the UTC day.
    An already complete timestamp is left as is.
    """
    if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T00:00:00.000Z"
    return value


class GoCardlessClient:
    """Read-only client for the GoCardless Pro API v2015-07-06."""

    BASE_URL = "https://api.gocardless.com"
    API_VERSION = "2015-07-06"

    def __init__(self, api_key: str = None, rate_limit_delay: float = 0.2):
        """
        Args:
            api_key: GoCardless Bearer token (or GOCARDLESS_API_KEY secret).
            rate_limit_delay: pause between paginated requests.
        """
        self.api_key = require(api_key, "GOCARDLESS_API_KEY")
        self.rate_limit_delay = rate_limit_delay
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "GoCardless-Version": self.API_VERSION,
            "Accept": "application/json",
        })

    # --- Primitive GET ---

    def fetch(self, endpoint: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        """GET on the API with retry on rate-limit (429)."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        for attempt in range(retries):
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                if not response.ok:
                    return {
                        "error": str(response.status_code),
                        "details": response.text,
                        "status_code": response.status_code,
                    }
                return response.json()
            except Exception as e:
                return {"error": str(e)}
        return {"error": "Max retries exceeded"}

    def _read(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """GET that RAISES on upstream refusal, where `fetch` returns an error dict.

        `fetch` keeps its own contract (only the connection probe calls it
        directly, to read its dict); every read goes through here, so
        that a refusal is never read as "nothing to read".
        """
        data = self.fetch(endpoint, params)
        if "error" not in data:
            return data
        code = data.get("status_code")
        if code is None:
            raise RuntimeError(f"gocardless: GET {endpoint} — {data['error']}")
        raise UpstreamHTTPError(code, data.get("details"), service="gocardless")

    def fetch_all(self, resource: str, params: Optional[dict] = None,
                  max_pages: Optional[int] = None) -> list:
        """GoCardless cursor pagination (`meta.cursors.after`).

        `resource` is the collection key (e.g. 'payments', 'events') that serves
        both as endpoint and as key in the response. A refusal on any
        page raises: a truncated collection is never returned as complete.
        """
        params = dict(params or {})
        out, pages, after = [], 0, None
        while True:
            if after:
                params["after"] = after
            data = self._read(resource, params)
            out.extend(data.get(resource, []))
            after = data.get("meta", {}).get("cursors", {}).get("after")
            pages += 1
            if not after or (max_pages and pages >= max_pages):
                break
            time.sleep(self.rate_limit_delay)
        return out

    # --- Simple reads ---

    def list_creditors(self) -> list:
        """GoCardless merchant accounts (the collecting account)."""
        return self._read("creditors")["creditors"]

    def list_payments(self, status: Optional[str] = None, limit: int = 50,
                      mandate: Optional[str] = None, customer: Optional[str] = None,
                      created_gt: Optional[str] = None) -> list:
        """List of direct debits (1 page).

        Args:
            status: status filter. Values: pending_submission, submitted,
                confirmed, paid_out, failed, cancelled, charged_back, etc.
            limit: page size (max 500 on the API side).
            mandate / customer: filters by link.
            created_gt: ISO8601, direct debits created after this date.
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        if mandate:
            params["mandate"] = mandate
        if customer:
            params["customer"] = customer
        if created_gt:
            params["created_at[gt]"] = _to_rfc3339(created_gt)
        return self._read("payments", params)["payments"]

    def get_payment(self, payment_id: str) -> dict:
        return self._read(f"payments/{payment_id}")["payments"]

    def get_mandate(self, mandate_id: str) -> dict:
        return self._read(f"mandates/{mandate_id}")["mandates"]

    def get_customer(self, customer_id: str) -> dict:
        return self._read(f"customers/{customer_id}")["customers"]

    def list_events(self, payment: Optional[str] = None, mandate: Optional[str] = None,
                    action: Optional[str] = None, resource_type: Optional[str] = None,
                    limit: int = 50) -> list:
        """Events (timeline). The reason for a failure: action='failed' on a payment."""
        params = {"limit": limit}
        if payment:
            params["payment"] = payment
        if mandate:
            params["mandate"] = mandate
        if action:
            params["action"] = action
        if resource_type:
            params["resource_type"] = resource_type
        return self._read("events", params)["events"]

    # --- Payouts ---

    def list_payouts(self, status: Optional[str] = None, limit: int = 50,
                     currency: Optional[str] = None, reference: Optional[str] = None,
                     created_gt: Optional[str] = None,
                     created_lt: Optional[str] = None) -> list:
        """Bank payouts (1 page). Amounts in cents.

        Args:
            status: pending, paid, bounced.
            limit: page size (max 500 on the API side).
            currency: ISO 4217 code (EUR, GBP…).
            reference: exact label carried on the bank statement.
            created_gt / created_lt: ISO8601, bounds on the payout's
                creation (exclusive). A bare date means midnight UTC.
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        if currency:
            params["currency"] = currency
        if reference:
            params["reference"] = reference
        if created_gt:
            params["created_at[gt]"] = _to_rfc3339(created_gt)
        if created_lt:
            params["created_at[lt]"] = _to_rfc3339(created_lt)
        return self._read("payouts", params)["payouts"]

    def get_payout(self, payout_id: str) -> dict:
        return self._read(f"payouts/{payout_id}")["payouts"]

    def list_payout_items(self, payout_id: str) -> list:
        """All the lines of a payout, all pages read.

        A line = `type` (payment_paid_out, payment_failed,
        payment_charged_back, payment_refunded, refund, refund_funds_returned,
        gocardless_fee, app_fee, revenue_share, surcharge_fee), signed `amount`
        in cents, `links` (payment, mandate, refund) and `taxes`.
        ⚠️ GoCardless only serves the lines of payouts created less than
        6 months ago: beyond that, HTTP 410.
        """
        return self.fetch_all("payout_items", {"payout": payout_id, "limit": 500})

    def payout_detail(self, payout_id: str) -> dict:
        """A payout and all its lines, to reconcile it payment by
        payment. Raw API amounts, in cents."""
        return {
            "payout": self.get_payout(payout_id),
            "items": self.list_payout_items(payout_id),
        }

    # --- Business aggregates ---

    def payment_party(self, payment_id: str) -> dict:
        """Resolves the payment → mandate → customer chain for a direct debit.

        Returns a flattened dict: amount, status, dates, and the counterparty
        (email, name/company, metadata). ⚠️ GoCardless metadata does not
        necessarily contain an external customer identifier (case observed at a client: empty).
        """
        p = self.get_payment(payment_id)
        mandate_id = p.get("links", {}).get("mandate")
        mandate = self.get_mandate(mandate_id) if mandate_id else {}
        customer_id = mandate.get("links", {}).get("customer")
        customer = self.get_customer(customer_id) if customer_id else {}
        name = customer.get("company_name") or " ".join(
            filter(None, [customer.get("given_name"), customer.get("family_name")])
        )
        return {
            "payment_id": payment_id,
            "amount": p.get("amount", 0) / 100,
            "currency": p.get("currency"),
            "status": p.get("status"),
            "charge_date": p.get("charge_date"),
            "created_at": p.get("created_at"),
            "mandate_id": mandate_id,
            "mandate_status": mandate.get("status"),
            "scheme": mandate.get("scheme"),
            "customer_id": customer_id,
            "email": customer.get("email"),
            "name": name,
            "metadata": customer.get("metadata", {}),
        }

    def failed_payments(self, since: Optional[str] = None, limit: int = 200) -> list:
        """Enriched failed direct debits, in a single agent call.

        Does the plumbing on the tool side: lists the `failed`, then for each
        row resolves mandate → customer (name/email) and the failure reason
        (Events API). The payment already comes from the list, so only
        mandate + customer + events are re-fetched per row.

        ⚠️ Facts only — no action decided here. "Retry vs redo
        a mandate" is a business judgment that stays with the agent/the doctrine.

        Args:
            since: ISO8601 (e.g. '2026-05-25'), filter on the creation date.
                Note: a payment created before `since` but failed after does not
                show up (the GCL API filters on created_at).
            limit: page size of the `failed` to enrich (max 500).
        """
        payments = self.list_payments(status="failed", limit=limit, created_gt=since)

        # 3 SEQUENTIAL requests per row (mandate, customer, reason) + a pause:
        # on 200 failures, 600 round trips in single file — 186 s measured in
        # prod, the only tool still able to hold a worker for three minutes.
        # Two levers, no contract change:
        #  - memoize mandate/customer, because failures CLUSTER (the same debtor
        #    misses several debits, and the monthly retries replay the same
        #    mandate) — duplicates no longer cost anything;
        #  - enrich in parallel bounded to 5, which stays far below GoCardless's
        #    cap (1000 req/min) and makes the per-row pause unnecessary.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Lock

        cache: dict[str, dict] = {}
        lock = Lock()

        def _cached(key: str, fetch, ident: Optional[str]) -> dict:
            if not ident:
                return {}
            k = f"{key}:{ident}"
            with lock:
                hit = cache.get(k)
            if hit is not None:
                return hit
            got = fetch(ident) or {}
            with lock:
                cache[k] = got
            return got

        def _row(p: dict) -> dict:
            mandate_id = p.get("links", {}).get("mandate")
            mandate = _cached("mandate", self.get_mandate, mandate_id)
            customer = _cached("customer", self.get_customer,
                               mandate.get("links", {}).get("customer"))
            name = customer.get("company_name") or " ".join(
                filter(None, [customer.get("given_name"), customer.get("family_name")])
            )
            fail = self.failure_reason(p["id"])   # per payment: never shareable
            return {
                "payment_id": p["id"],
                "name": name,
                "email": customer.get("email"),
                "amount": p.get("amount", 0) / 100,
                "currency": p.get("currency"),
                "charge_date": p.get("charge_date"),
                "failed_at": fail.get("created_at"),
                "cause": fail.get("cause"),
                "reason_code": fail.get("reason_code"),
                "will_attempt_retry": fail.get("will_attempt_retry"),
                "mandate_id": mandate_id,
                "mandate_status": mandate.get("status"),
            }

        with ThreadPoolExecutor(max_workers=5) as pool:
            rows = list(pool.map(_row, payments))
        rows.sort(key=lambda r: r.get("failed_at") or "", reverse=True)
        return rows

    def failure_reason(self, payment_id: str) -> dict:
        """Reason for a direct debit's latest failure (Events API).

        Returns cause/description/reason_code/will_attempt_retry. If
        `will_attempt_retry` is True, GoCardless will retry — do not issue
        a credit note until it is False.
        """
        events = self.list_events(payment=payment_id, action="failed", limit=10)
        if not events:
            return {"failed": False}
        ev = events[0]  # the most recent
        d = ev.get("details", {})
        return {
            "failed": True,
            "created_at": ev.get("created_at"),
            "cause": d.get("cause"),
            "description": d.get("description"),
            "reason_code": d.get("reason_code"),
            "scheme": d.get("scheme"),
            "will_attempt_retry": d.get("will_attempt_retry"),
        }
