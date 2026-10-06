"""Stripe API client (v1, https://api.stripe.com) — billing, subscriptions,
invoices, payments, catalog.

Bearer token (`Authorization: Bearer <key>`). Stripe documents HTTP Basic with
the key as username and an empty password (`-u sk_live_xxx:`); the docs state
bearer is equivalent server-side, and it is what every other client here uses.

**Requests are FORM-ENCODED** (`application/x-www-form-urlencoded`), not JSON —
this is the single most common way a Stripe integration written from memory is
wrong. Nested values use Stripe's bracket syntax (`created[gte]=…`,
`expand[]=customer`, `line_items[0][price]=…`), produced by `_encode` below and
applied identically to query strings and bodies. Responses are JSON.

**No money-moving method exists in this client, deliberately.** Refunds, payouts
(create/cancel/reverse), dispute close and evidence submission, subscription
cancellation, invoice finalize/pay/void, customer delete and charge capture are
all absent — not merely unexposed at the tool layer. This departs from the
`AhrefsClient` doctrine ("implement the full API, let the tool layer choose"),
and the reason is that Ahrefs' destructive endpoints delete a keyword list while
these move real money irreversibly. A method that exists is a method a future
tool can be pointed at in one line; a method that does not exist forces the
decision back through review. `oto_mcp/tools/stripe.py` therefore cannot refund,
and neither can anything built on this client without a deliberate PR here.

Two consequences of Stripe's own design that callers must know:

- **`limit` defaults to 10 on every list endpoint** and silently truncates. It is
  NOT defaulted here (a client that invents a limit hides the truncation one
  layer deeper); the tool layer sets it explicitly. Max is 100.
- **Only seven resources support `/search`** — charges, customers, invoices,
  payment_intents, subscriptions, prices, products. Search is eventually
  consistent (a just-created object may not appear for ~a minute) and paginates
  by an opaque `next_page` token, NOT by the `starting_after` cursor the list
  endpoints use. The two paginators are not interchangeable.

Every response carries a `Request-Id` header; `_request` attaches it to the
`UpstreamHTTPError` body on failure so a support escalation can quote it.

Verified against docs.stripe.com (api/authentication, api/pagination, api/errors,
search, keys) on 2026-08-22. **Not yet live-tested** — no key was available at
authoring time; this docstring is to be updated with the live-test date and
findings once one is, exactly as `GranolaClient` and `AhrefsClient` record theirs.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests

from ..common.credentials import require
from ..common import UpstreamHTTPError

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
_BASE_URL = "https://api.stripe.com"

# The seven resources that carry `/v1/<resource>/search` (docs.stripe.com/search).
# Any other value is a caller error, not an upstream 404 to decode.
SEARCHABLE = ("charges", "customers", "invoices", "payment_intents",
              "subscriptions", "prices", "products")


def _encode(value: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    """Flatten a Python value into key/value pairs in Stripe's bracket format.

    `{"created": {"gte": 1}}` → `[("created[gte]", 1)]` ;
    `{"expand": ["customer"]}` → `[("expand[]", "customer")]` ;
    `{"line_items": [{"price": "p"}]}` → `[("line_items[0][price]", "p")]`.

    `None` values are DROPPED (an omitted kwarg must not become the string
    "None"), and booleans are rendered "true"/"false" — Python would send "True",
    which Stripe reads as a non-empty string, hence as true: `active=False`
    would then silently filter on ACTIVE objects.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        out: List[Tuple[str, Any]] = []
        for k, v in value.items():
            out.extend(_encode(v, f"{prefix}[{k}]" if prefix else str(k)))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for i, v in enumerate(value):
            # A scalar goes in `key[]` (repeated), an object in `key[i][field]`.
            out.extend(_encode(v, f"{prefix}[{i}]" if isinstance(v, (dict, list, tuple))
                               else f"{prefix}[]"))
        return out
    if isinstance(value, bool):
        return [(prefix, "true" if value else "false")]
    return [(prefix, value)]


class StripeClient:
    """Stripe API v1 client (https://api.stripe.com), Bearer auth, form-encoded."""

    BASE_URL = _BASE_URL

    def __init__(self, api_key: Optional[str] = None, *,
                 api_version: Optional[str] = None,
                 stripe_account: Optional[str] = None):
        """
        Args:
            api_key: Stripe API key. A
                **restricted key** (`rk_test_…` / `rk_live_…`) is the right
                credential for this connector — it grants per-resource read
                permissions, so an operator can hand over reads without handing
                over the ability to move money. A secret key (`sk_…`) also
                works. A **publishable key (`pk_…`) is refused here**: it is
                client-side only and cannot read a single customer or invoice,
                so accepting it would defer a config mistake into a confusing
                401 at the first real call.
            api_version: optional `Stripe-Version` override. Omitted = the
                account's own default version, which is what the operator sees
                in their dashboard. Pinning is deliberately NOT the default:
                a pin that disagrees with the account silently changes response
                shapes (Stripe moved usage-based billing twice this way).
            stripe_account: optional `Stripe-Account` header (Connect) — act on
                a connected account rather than the key's own account.
        """
        self.api_key = require(api_key, "STRIPE_API_KEY")
        if self.api_key.startswith("pk_"):
            raise ValueError(
                "PUBLISHABLE Stripe key (`pk_…`): it is meant for the browser and "
                "cannot read any account data. Use a restricted key "
                "(`rk_…`, recommended) or a secret key (`sk_…`) — Stripe Dashboard → "
                "Developers → API keys.")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        if api_version:
            self.session.headers["Stripe-Version"] = api_version
        if stripe_account:
            self.session.headers["Stripe-Account"] = stripe_account

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None,
                 idempotency_key: Optional[str] = None) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}",
            params=_encode(params or {}) or None,
            data=_encode(body or {}) or None,
            headers=headers, timeout=_HTTP_TIMEOUT)
        if resp.status_code >= 400:
            # The `Request-Id` is the first thing Stripe support asks for;
            # without it the user has to dig the call out of the logs by hand.
            request_id = resp.headers.get("Request-Id")
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            if request_id and isinstance(payload, dict):
                payload = {**payload, "request_id": request_id}
            raise UpstreamHTTPError(resp.status_code, payload, service="stripe")
        return resp.json() if resp.content else {}

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: Dict[str, Any], *,
              idempotency_key: Optional[str] = None) -> Any:
        return self._request("POST", path, body=body, idempotency_key=idempotency_key)

    # ================================================================
    # Search — the SEVEN resources that support it
    # ================================================================

    def search(self, resource: str, query: str, **params: Any) -> Any:
        """GET /v1/{resource}/search — Stripe Query Language on one of the seven
        searchable resources.

        Args:
            resource: one of `SEARCHABLE` — charges, customers, invoices,
                payment_intents, subscriptions, prices, products.
            query: Stripe's SQL-like query, e.g.
                `status:"active" AND created>1704067200`,
                `email~"acme.com"`, `metadata["order_id"]:"6735"`.
                Operators: `:` (equals), `~` (contains / starts with depending on
                the field), `>` `<` `>=` `<=` on numbers and dates, `AND`/`OR`,
                `-` (negation). Searchable fields differ PER resource.
            **params: `limit` (1-100, default 10), `page` (opaque
                `next_page` token returned by the previous call — NOT `starting_after`),
                `expand`.

        Note: search is eventually consistent — an object created just now
        may take up to a minute to appear. To read an object you have just
        written, go through its id.
        """
        if resource not in SEARCHABLE:
            raise ValueError(
                f"Stripe can only search in {', '.join(SEARCHABLE)} "
                f"(got {resource!r}). Other resources are listed with "
                "filters, not with a query.")
        return self._get(f"/v1/{resource}/search", query=query, **params)

    # ================================================================
    # Balance, balance transactions, payouts — "how much do we have"
    # ================================================================

    def balance(self) -> Any:
        """GET /v1/balance — `available` and `pending` balance per currency. No
        parameters, a single small response: it is also the auth probe."""
        return self._get("/v1/balance")

    def list_balance_transactions(self, **params: Any) -> Any:
        """GET /v1/balance_transactions — every movement OF the balance, with its
        `fee` and `net`. This is the right source for "how much have we
        actually collected" (what was invoiced lives on the invoices).

        Args:
            **params: `created` (dict `gte`/`gt`/`lte`/`lt`, Unix timestamps),
                `currency`, `payout` (the contents of a specific payout), `source`,
                `type`, `limit`, `starting_after`, `ending_before`, `expand`.
        """
        return self._get("/v1/balance_transactions", **params)

    def get_balance_transaction(self, txn_id: str) -> Any:
        """GET /v1/balance_transactions/{id}."""
        return self._get(f"/v1/balance_transactions/{txn_id}")

    def list_payouts(self, **params: Any) -> Any:
        """GET /v1/payouts — transfers to the bank account: "when
        does the next deposit arrive, and for how much".

        Args:
            **params: `status`, `arrival_date` (dict of operators), `created`,
                `destination`, `limit`, `starting_after`, `ending_before`.
        """
        return self._get("/v1/payouts", **params)

    def get_payout(self, payout_id: str) -> Any:
        """GET /v1/payouts/{id}. The DETAIL of a payout is read with
        `list_balance_transactions(payout=payout_id)`."""
        return self._get(f"/v1/payouts/{payout_id}")

    # ================================================================
    # Customers
    # ================================================================

    def list_customers(self, **params: Any) -> Any:
        """GET /v1/customers — filters `email` (EXACT equality), `created`,
        `limit`, `starting_after`, `ending_before`, `expand`. For a partial
        match, `search("customers", 'email~"acme.com"')`."""
        return self._get("/v1/customers", **params)

    def get_customer(self, customer_id: str, **params: Any) -> Any:
        """GET /v1/customers/{id}. `expand` accepts in particular
        `subscriptions`, `default_source`, `tax`."""
        return self._get(f"/v1/customers/{customer_id}", **params)

    def create_customer(self, **body: Any) -> Any:
        """POST /v1/customers — `email`, `name`, `phone`, `description`,
        `address`, `metadata`, `preferred_locales`. No field is required
        by Stripe, but a customer without an `email` cannot be found afterwards."""
        return self._post("/v1/customers", body)

    def update_customer(self, customer_id: str, **body: Any) -> Any:
        """POST /v1/customers/{id} — same fields as creation. Only the
        fields provided change."""
        return self._post(f"/v1/customers/{customer_id}", body)

    def list_customer_payment_methods(self, customer_id: str, **params: Any) -> Any:
        """GET /v1/customers/{id}/payment_methods — the saved payment
        methods. `card.exp_month`/`exp_year` explain most involuntary
        unpaid invoices (expired card); an EMPTY list explains the rest.

        Args:
            **params: `type` (card, sepa_debit…), `limit`.
        """
        return self._get(f"/v1/customers/{customer_id}/payment_methods", **params)

    # ================================================================
    # Subscriptions
    # ================================================================

    def list_subscriptions(self, **params: Any) -> Any:
        """GET /v1/subscriptions.

        Args:
            **params: `customer`, `price`, `status` (incomplete,
                incomplete_expired, trialing, active, past_due, canceled,
                unpaid, paused, ended, or `all` — WITHOUT `status`, Stripe returns
                only active and trialing subscriptions, which silently
                hides the canceled ones), `collection_method`,
                `created`, `current_period_end`, `current_period_start`,
                `limit`, `starting_after`, `ending_before`, `expand`.
        """
        return self._get("/v1/subscriptions", **params)

    def get_subscription(self, subscription_id: str, **params: Any) -> Any:
        """GET /v1/subscriptions/{id}."""
        return self._get(f"/v1/subscriptions/{subscription_id}", **params)

    def list_subscription_items(self, subscription: str, **params: Any) -> Any:
        """GET /v1/subscription_items?subscription=… — the lines of a
        subscription, where the price and the quantity (seats) actually live."""
        return self._get("/v1/subscription_items", subscription=subscription, **params)

    # ================================================================
    # Invoices
    # ================================================================

    def list_invoices(self, **params: Any) -> Any:
        """GET /v1/invoices.

        Args:
            **params: `customer`, `subscription`, `status` (draft, open, paid,
                uncollectible, void), `collection_method`, `created`, `due_date`,
                `limit`, `starting_after`, `ending_before`, `expand`.
        """
        return self._get("/v1/invoices", **params)

    def get_invoice(self, invoice_id: str, **params: Any) -> Any:
        """GET /v1/invoices/{id}."""
        return self._get(f"/v1/invoices/{invoice_id}", **params)

    def get_invoice_lines(self, invoice_id: str, **params: Any) -> Any:
        """GET /v1/invoices/{id}/lines — the line-by-line detail."""
        return self._get(f"/v1/invoices/{invoice_id}/lines", **params)

    def create_invoice(self, **body: Any) -> Any:
        """POST /v1/invoices — creates a DRAFT invoice. It is neither
        finalized nor sent nor collected by this call: nothing goes to the customer
        and nothing is charged (`finalize`/`pay` are not implemented here).

        Args:
            **body: `customer` (required in practice), `auto_advance` (leave
                False to stay in control), `collection_method`, `description`,
                `days_until_due`, `metadata`, `currency`.
        """
        return self._post("/v1/invoices", body)

    def update_invoice(self, invoice_id: str, **body: Any) -> Any:
        """POST /v1/invoices/{id} — edits an invoice. Once FINALIZED,
        Stripe only accepts `metadata`, `description` and a few ancillary
        fields; the rest is rejected on the API side."""
        return self._post(f"/v1/invoices/{invoice_id}", body)

    def list_invoice_items(self, **params: Any) -> Any:
        """GET /v1/invoiceitems — filters `customer`, `invoice`, `pending`,
        `created`."""
        return self._get("/v1/invoiceitems", **params)

    def create_invoice_item(self, **body: Any) -> Any:
        """POST /v1/invoiceitems — adds a one-off amount to a customer's NEXT
        invoice (or to a named draft invoice). A negative `amount`
        is a commercial gesture (credit).

        Args:
            **body: `customer` (required), `amount` (in the smallest unit —
                cents) + `currency`, or `price`/`quantity`; `invoice` to
                target a specific draft, `description`, `metadata`.
        """
        return self._post("/v1/invoiceitems", body)

    # ================================================================
    # Payments — intents, charges, refunds, disputes
    # ================================================================

    def list_payment_intents(self, **params: Any) -> Any:
        """GET /v1/payment_intents — filters `customer`, `created`, `limit`,
        `starting_after`, `ending_before`, `expand`."""
        return self._get("/v1/payment_intents", **params)

    def get_payment_intent(self, payment_intent_id: str, **params: Any) -> Any:
        """GET /v1/payment_intents/{id}. `last_payment_error` carries the
        authoritative failure reason (`code`, `decline_code`, `message`)."""
        return self._get(f"/v1/payment_intents/{payment_intent_id}", **params)

    def list_charges(self, **params: Any) -> Any:
        """GET /v1/charges — filters `customer`, `created`, `payment_intent`,
        `limit`, `starting_after`, `ending_before`, `expand`."""
        return self._get("/v1/charges", **params)

    def get_charge(self, charge_id: str, **params: Any) -> Any:
        """GET /v1/charges/{id}. On failure, `failure_code`/`failure_message` and
        above all `outcome.seller_message`, written for the merchant."""
        return self._get(f"/v1/charges/{charge_id}", **params)

    def list_refunds(self, **params: Any) -> Any:
        """GET /v1/refunds — READ the refunds already issued (filters
        `charge`, `payment_intent`, `created`). Issuing one is not
        possible from this client, by construction."""
        return self._get("/v1/refunds", **params)

    def get_refund(self, refund_id: str, **params: Any) -> Any:
        """GET /v1/refunds/{id}."""
        return self._get(f"/v1/refunds/{refund_id}", **params)

    def list_disputes(self, **params: Any) -> Any:
        """GET /v1/disputes — filters `charge`, `payment_intent`, `created`.
        `evidence_details.due_by` is a hard deadline: past it, the dispute is
        lost by default, and the money has already been withdrawn from the balance in the meantime."""
        return self._get("/v1/disputes", **params)

    def get_dispute(self, dispute_id: str, **params: Any) -> Any:
        """GET /v1/disputes/{id}."""
        return self._get(f"/v1/disputes/{dispute_id}", **params)

    # ================================================================
    # Catalog — products, prices, payment links, discounts
    # ================================================================

    def list_products(self, **params: Any) -> Any:
        """GET /v1/products — filters `active`, `ids`, `shippable`, `url`,
        `created`, `limit`, `starting_after`, `ending_before`."""
        return self._get("/v1/products", **params)

    def get_product(self, product_id: str, **params: Any) -> Any:
        """GET /v1/products/{id}."""
        return self._get(f"/v1/products/{product_id}", **params)

    def create_product(self, **body: Any) -> Any:
        """POST /v1/products — `name` (required), `description`, `active`,
        `metadata`, `images`, `url`, `default_price_data`."""
        return self._post("/v1/products", body)

    def update_product(self, product_id: str, **body: Any) -> Any:
        """POST /v1/products/{id} — same fields. `active=False` takes the
        product off sale without deleting anything."""
        return self._post(f"/v1/products/{product_id}", body)

    def list_prices(self, **params: Any) -> Any:
        """GET /v1/prices — filters `product`, `active`, `currency`, `type`
        (one_time | recurring), `lookup_keys`, `recurring` (dict, e.g.
        `{"interval": "month"}`), `created`, `limit`, `expand`."""
        return self._get("/v1/prices", **params)

    def get_price(self, price_id: str, **params: Any) -> Any:
        """GET /v1/prices/{id}."""
        return self._get(f"/v1/prices/{price_id}", **params)

    def create_price(self, **body: Any) -> Any:
        """POST /v1/prices — `currency` + `product` required, plus `unit_amount`
        (cents) or `custom_unit_amount`. `recurring` (dict `interval`,
        `interval_count`) makes it a subscription price.

        ⚠️ A Stripe price is **immutable** on its amount: "changing the price"
        is done by creating a new price and deactivating the old one
        (`update_price(active=False)`), never by modifying this one.
        """
        return self._post("/v1/prices", body)

    def update_price(self, price_id: str, **body: Any) -> Any:
        """POST /v1/prices/{id} — only `active`, `metadata`, `nickname`,
        `lookup_key` and the pricing options are modifiable; the
        amount is not (see `create_price`)."""
        return self._post(f"/v1/prices/{price_id}", body)

    def list_payment_links(self, **params: Any) -> Any:
        """GET /v1/payment_links — filter `active`, `limit`, `starting_after`."""
        return self._get("/v1/payment_links", **params)

    def get_payment_link(self, payment_link_id: str, **params: Any) -> Any:
        """GET /v1/payment_links/{id}."""
        return self._get(f"/v1/payment_links/{payment_link_id}", **params)

    def get_payment_link_line_items(self, payment_link_id: str, **params: Any) -> Any:
        """GET /v1/payment_links/{id}/line_items — what the link charges for."""
        return self._get(f"/v1/payment_links/{payment_link_id}/line_items", **params)

    def create_payment_link(self, line_items: List[Dict[str, Any]], **body: Any) -> Any:
        """POST /v1/payment_links — a reusable URL that opens a payment page
        HOSTED BY STRIPE. It is the safest way to get
        something paid: no card number passes through oto, and the
        link charges nobody until a human opens it.

        Args:
            line_items: list of `{"price": "price_…", "quantity": n}` (required).
            **body: `after_completion`, `allow_promotion_codes`,
                `currency`, `metadata`, `customer_creation`.
        """
        return self._post("/v1/payment_links", {"line_items": line_items, **body})

    def update_payment_link(self, payment_link_id: str, **body: Any) -> Any:
        """POST /v1/payment_links/{id} — notably `active=False` to
        deactivate a link without deleting it."""
        return self._post(f"/v1/payment_links/{payment_link_id}", body)

    def list_coupons(self, **params: Any) -> Any:
        """GET /v1/coupons — the discount RULE (percent_off/amount_off,
        duration). The code a customer types is a `promotion_code`."""
        return self._get("/v1/coupons", **params)

    def get_coupon(self, coupon_id: str, **params: Any) -> Any:
        """GET /v1/coupons/{id}."""
        return self._get(f"/v1/coupons/{coupon_id}", **params)

    def create_coupon(self, **body: Any) -> Any:
        """POST /v1/coupons — `duration` (once | repeating | forever) required,
        plus `percent_off` OR `amount_off`+`currency` (Stripe rejects both
        at once). `duration_in_months` required if `duration="repeating"`.
        `id` sets the identifier (otherwise Stripe generates one); `name` is what
        a customer sees on their invoice. `max_redemptions`/`redeem_by`
        bound usage in time/in volume."""
        return self._post("/v1/coupons", body)

    def update_coupon(self, coupon_id: str, **body: Any) -> Any:
        """POST /v1/coupons/{id} — a Stripe Coupon has ONLY `name` and
        `metadata` modifiable after creation (amount/duration/
        redemptions are frozen, like the amount of a `Price`); neither
        deletion nor deactivation exists in this client (see the
        module's top docstring) — a coupon you no longer want is
        retired by revoking its `promotion_code`s (`update_promotion_code`,
        `active=False`), not by touching it."""
        return self._post(f"/v1/coupons/{coupon_id}", body)

    def list_promotion_codes(self, **params: Any) -> Any:
        """GET /v1/promotion_codes — filters `code`, `coupon`, `active`,
        `customer`, `created`."""
        return self._get("/v1/promotion_codes", **params)

    def get_promotion_code(self, promotion_code_id: str, **params: Any) -> Any:
        """GET /v1/promotion_codes/{id}."""
        return self._get(f"/v1/promotion_codes/{promotion_code_id}", **params)

    def create_promotion_code(self, **body: Any) -> Any:
        """POST /v1/promotion_codes — `coupon` (required) is the discount
        rule; what this endpoint adds is the CODE a customer actually types
        at payment. `code` sets the text (uppercase/digits —
        Stripe generates one otherwise), `customer` restricts the code to a single
        customer, `max_redemptions`/`expires_at` bound usage,
        `restrictions` (dict, e.g. `{"minimum_amount": 5000,
        "minimum_amount_currency": "eur"}` or
        `{"first_time_transaction": True}`) bounds WHEN it applies.

        ⚠️ **Verified LIVE on 2026-08-23** against a real test account (account
        API version `2026-07-29.dahlia`, the default version of a
        new account): a FLAT `coupon=<id>` is REJECTED (`400
        parameter_unknown: coupon`) — this API version replaced the field
        with a nested `promotion` object (`promotion[type]=coupon`,
        `promotion[coupon]=<id>`), mirroring the RESPONSE shape that
        `list_promotion_codes`/`get_promotion_code` already return on this same
        version (`promotion: {coupon, type}`, no flat `coupon` either
        on read). All the other fields (`code`, `customer`,
        `max_redemptions`, `expires_at`, `restrictions`, `metadata`)
        keep working as-is alongside it. The signature of THIS
        method stays `coupon=<id>` on the caller side (nothing changes for
        `oto_mcp/tools/stripe.py`) — it is HERE, when emitting the
        request, that `coupon` is turned into `promotion`. An account
        pinned to an API version PRIOR to this change (via the
        `api_version` credential field) might expect the old flat form
        instead — not verified for lack of such an account;
        see the module's top docstring on this risk."""
        body = dict(body)
        coupon = body.pop("coupon", None)
        if coupon is not None:
            body["promotion"] = {"type": "coupon", "coupon": coupon}
        return self._post("/v1/promotion_codes", body)

    def update_promotion_code(self, promotion_code_id: str, **body: Any) -> Any:
        """POST /v1/promotion_codes/{id} — only `active` and `metadata`
        are modifiable after creation (the code, the linked coupon and the
        restrictions are frozen). `active=False` is the way to REVOKE
        a code without deleting it (no deletion exists in this
        client) — redemptions already made are not affected."""
        return self._post(f"/v1/promotion_codes/{promotion_code_id}", body)

    # ================================================================
    # Checkout — hosted sessions
    # ================================================================

    def list_checkout_sessions(self, **params: Any) -> Any:
        """GET /v1/checkout/sessions — filters `customer`, `payment_intent`,
        `subscription`, `status` (open | complete | expired), `created`.
        `status=open` = abandoned carts still open."""
        return self._get("/v1/checkout/sessions", **params)

    def get_checkout_session(self, session_id: str, **params: Any) -> Any:
        """GET /v1/checkout/sessions/{id}."""
        return self._get(f"/v1/checkout/sessions/{session_id}", **params)

    def get_checkout_session_line_items(self, session_id: str, **params: Any) -> Any:
        """GET /v1/checkout/sessions/{id}/line_items."""
        return self._get(f"/v1/checkout/sessions/{session_id}/line_items", **params)

    # ================================================================
    # Events — the account's activity feed
    # ================================================================

    def list_events(self, **params: Any) -> Any:
        """GET /v1/events — every state change on the account, most recent
        first, the object concerned included in `data.object`. Stripe retention:
        30 days.

        Args:
            **params: `type` (accepts the wildcard, e.g. `invoice.*`), `types`
                (list), `created`, `limit`, `starting_after`, `ending_before`.
        """
        return self._get("/v1/events", **params)

    def get_event(self, event_id: str, **params: Any) -> Any:
        """GET /v1/events/{id}."""
        return self._get(f"/v1/events/{event_id}", **params)

    # ================================================================
    # Webhooks — READ only (integration diagnostics)
    # ================================================================

    def list_webhook_endpoints(self, **params: Any) -> Any:
        """GET /v1/webhook_endpoints — which integrations listen to this account.
        Creating, modifying or deleting them is not implemented: it is
        infrastructure configuration, and a deletion silently breaks the
        automation that depended on it (dunning, provisioning)."""
        return self._get("/v1/webhook_endpoints", **params)
