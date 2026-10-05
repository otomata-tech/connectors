"""Mailpool API client — cold-email infrastructure: domains, DNS, mailboxes,
spam checks, warmup.

API v1 (`https://app.mailpool.io/v1/api`, reference https://api.mailpool.ai),
authenticated by the **`X-Api-Authorization: <key>`** header. A key belongs to
a workspace and is created in Mailpool → Settings → API Keys. One method = one
endpoint.

Scope: reading domains, their DNS records and owners, mailboxes, warmup and
subscription slots; editing a domain's DNS and DMARC settings; running spam
checks. Everything that is BILLED is deliberately absent: registering,
transferring or renewing a domain, creating a mailbox, changing subscription
slots, adding an inbox to warmup, inbox-placement tests. So are exports and the
platform credentials they rely on.

What the caller needs to know, and cannot guess:

- ⚠️ **This client never returns a mailbox credential.** Mailbox payloads —
  including the one nested in a spam check — carry the mailbox password, the
  IMAP/SMTP passwords, the admin account's password and secret. Every key
  matching `SECRET_KEYS` is removed, at any depth, before a payload is
  returned (`strip_secrets`).

- **Every list requires `limit` and `offset`** (a list called without them is
  refused with 400 "numeric string is expected"). They are always sent.

- ⚠️ **`update_domain_dns` REPLACES the whole record set** of the domain: a
  record left out of the list is deleted (MX, SPF, DKIM included). Read with
  `get_domain_dns`, change, send everything back. Changes reach the public
  nameservers within minutes.

- **The `_dmarc` TXT record is published from the domain's DMARC settings**, not
  from the record set: an edited `_dmarc` record is stored by
  `update_domain_dns` but only goes live after `set_dmarc_email` or
  `set_dmarc_policy`. `set_dmarc_email` writes the address as both `rua` and
  `ruf`.

- `get_domain_dns` returns the configuration stored by Mailpool, not a live DNS
  lookup.

- **Some features are enabled per workspace**: the Google Workspace / Microsoft
  365 availability checks answer 403 "not enabled for this workspace" otherwise.

- A spam check is free and completes within seconds (`state` goes from
  `pending` to `completed`); it is refused with 404 for mailbox types it does
  not support.

Requires: requests
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

# (connect, read) — never an unbounded wait, and short enough that a consumer
# with a ~45 s budget per call gets an answer (a domain lookup can hang).
HTTP_TIMEOUT = (10, 30)

MIN_LIMIT, MAX_LIMIT = 1, 100
DEFAULT_LIMIT = 50

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

DMARC_POLICIES = ("none", "quarantine", "reject")
DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV")

#: Keys removed from every payload, at any depth: mailbox, IMAP/SMTP and admin
#: passwords, admin secrets, auth codes, platform API keys.
SECRET_KEYS = re.compile(r"pass(word)?|secret|auth_?code|api_?key|token",
                         re.IGNORECASE)


def strip_secrets(payload: Any) -> Any:
    """`payload` without any key matching `SECRET_KEYS`, at any depth."""
    if isinstance(payload, dict):
        return {k: strip_secrets(v) for k, v in payload.items()
                if not SECRET_KEYS.search(str(k))}
    if isinstance(payload, list):
        return [strip_secrets(v) for v in payload]
    return payload


class MailpoolClient:
    """Mailpool v1 client (https://app.mailpool.io/v1/api), `X-Api-Authorization` auth."""

    BASE_URL = "https://app.mailpool.io/v1/api"

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Mailpool workspace API key, passed by the consumer
                (Mailpool → Settings → API Keys).
        """
        self.api_key = require(api_key, "MAILPOOL_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Api-Authorization": self.api_key,
            "Accept": "application/json",
        })

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _page(limit: Optional[int], offset: Optional[int]) -> Dict[str, int]:
        limit = DEFAULT_LIMIT if limit is None else limit
        offset = 0 if offset is None else offset
        for name, value in (("limit", limit), ("offset", offset)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"`{name}` must be an integer.")
        if not (MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(
                f"`limit` must be between {MIN_LIMIT} and {MAX_LIMIT}; got "
                f"{limit}. Beyond that, paginate with `offset`.")
        if offset < 0:
            raise ValueError("`offset` must be >= 0.")
        return {"limit": limit, "offset": offset}

    @staticmethod
    def _id(name: str, value: Any) -> int:
        """A numeric id placed IN the path: an int, never a string that could
        reach another endpoint."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"`{name}` must be a positive integer.")
        return value

    @staticmethod
    def _domain_name(value: Any) -> str:
        if not isinstance(value, str) or "." not in value.strip():
            raise ValueError("`domain` must be a domain name such as example.com.")
        return value.strip().lower()

    @staticmethod
    def _check_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records = list(records)
        if not records:
            raise ValueError(
                "`records` is empty: the call replaces the whole record set and "
                "would delete every record of the domain.")
        out = []
        for i, r in enumerate(records):
            if not isinstance(r, dict):
                raise ValueError(f"records[{i}] must be an object.")
            if r.get("type") not in DNS_RECORD_TYPES:
                raise ValueError(
                    f"records[{i}].type must be one of {', '.join(DNS_RECORD_TYPES)}.")
            if not isinstance(r.get("value"), str) or not r["value"]:
                raise ValueError(f"records[{i}].value must be a non-empty string.")
            unknown = set(r) - {"type", "key", "value", "priority"}
            if unknown:
                raise ValueError(
                    f"records[{i}] has unknown fields: {', '.join(sorted(unknown))}.")
            out.append(dict(r))
        return out

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # Retry 429/5xx for READS only: a replayed POST could act twice.
        retryable = method.upper() in ("GET", "HEAD")
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=params or None,
                json=json, timeout=HTTP_TIMEOUT)
            if (last.status_code not in RETRY_STATUSES
                    or not retryable or attempt == MAX_ATTEMPTS - 1):
                break
            time.sleep(float(2 ** attempt))
        raise_for_upstream(last, service="mailpool")
        if not last.content:
            return {}
        try:
            body = last.json()
        except ValueError:
            return last.text
        return strip_secrets(body)

    # --- probe --------------------------------------------------------------

    def probe(self) -> Any:
        """Check that the key authenticates, for the cost of one call (401 if
        wrong): the subscription slots, no parameter, no personal data."""
        return self.get_subscription_slots()

    # --- domains ------------------------------------------------------------

    def list_domains(self, limit: Optional[int] = None,
                     offset: Optional[int] = None) -> Any:
        """`GET /domains/` — `{data: [...], total}`."""
        return self._request("GET", "/domains/", params=self._page(limit, offset))

    def get_domain(self, domain_id: int) -> Any:
        """`GET /domains/{id}`."""
        return self._request("GET", f"/domains/{self._id('domain_id', domain_id)}")

    def get_domain_dns(self, domain_id: int) -> Any:
        """`GET /domains/{id}/dns` — list of `{type, key, value, priority?}`;
        `key` is the host label (`null` for the apex)."""
        return self._request(
            "GET", f"/domains/{self._id('domain_id', domain_id)}/dns")

    def update_domain_dns(self, domain_id: int,
                          records: Iterable[Dict[str, Any]]) -> Any:
        """`PUT /domains/{id}/dns` — ⚠️ REPLACES the whole record set."""
        return self._request(
            "PUT", f"/domains/{self._id('domain_id', domain_id)}/dns",
            json=self._check_records(records))

    def set_dmarc_email(self, domain_id: int, email: str) -> Any:
        """`POST /domains/{id}/dmarc-email` — report address (`rua` and `ruf`);
        also publishes the stored `_dmarc` record."""
        if not isinstance(email, str) or "@" not in email:
            raise ValueError("`email` must be an email address.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/dmarc-email",
            json={"email": email})

    def set_dmarc_policy(self, domain_id: int, policy: str) -> Any:
        """`POST /domains/{id}/dmarc-policy` — `none`, `quarantine` or `reject`."""
        if policy not in DMARC_POLICIES:
            raise ValueError(
                f"`policy` must be one of {', '.join(DMARC_POLICIES)}.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/dmarc-policy",
            json={"policy": policy})

    def set_redirect_url(self, domain_id: int, url: str) -> Any:
        """`POST /domains/{id}/redirect-url` — where a visit to the domain goes."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("`url` must be a non-empty string.")
        return self._request(
            "POST", f"/domains/{self._id('domain_id', domain_id)}/redirect-url",
            json={"url": url.strip()})

    def get_domain_info(self, domain: str) -> Any:
        """`GET /domains/info` — `{available, price}` of a domain to register."""
        return self._request("GET", "/domains/info",
                             params={"domain": self._domain_name(domain)})

    def get_domain_suggestions(self, domain: str, limit: Optional[int] = None,
                               offset: Optional[int] = None) -> Any:
        """`GET /domains/suggestions` — available variants of a name, with price."""
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("`domain` must be a non-empty string.")
        return self._request(
            "GET", "/domains/suggestions",
            params={"domain": domain.strip(), **self._page(limit, offset)})

    def check_google_workspace_availability(self, domain: str) -> Any:
        """`POST /domains/google-workspace-availability` (enabled per workspace)."""
        return self._request("POST", "/domains/google-workspace-availability",
                             json={"domain": self._domain_name(domain)})

    def check_microsoft_365_availability(self, domain: str) -> Any:
        """`POST /domains/microsoft-365-availability` (enabled per workspace)."""
        return self._request("POST", "/domains/microsoft-365-availability",
                             json={"domain": self._domain_name(domain)})

    def list_domain_owners(self) -> Any:
        """`GET /domains/owners` — saved registrant contacts."""
        return self._request("GET", "/domains/owners")

    # --- mailboxes ----------------------------------------------------------

    def list_mailboxes(self, limit: Optional[int] = None,
                       offset: Optional[int] = None,
                       domain_id: Optional[int] = None) -> Any:
        """`GET /mailboxes` — `{data: [...], total}`, credentials removed."""
        params: Dict[str, Any] = dict(self._page(limit, offset))
        if domain_id is not None:
            params["domainId"] = self._id("domain_id", domain_id)
        return self._request("GET", "/mailboxes", params=params)

    def get_mailbox(self, mailbox_id: int) -> Any:
        """`GET /mailboxes/{id}` — credentials removed."""
        return self._request(
            "GET", f"/mailboxes/{self._id('mailbox_id', mailbox_id)}")

    # --- spam checks --------------------------------------------------------

    def list_spam_checks(self, limit: Optional[int] = None,
                         offset: Optional[int] = None) -> Any:
        """`GET /spam-checks/`."""
        return self._request("GET", "/spam-checks/",
                             params=self._page(limit, offset))

    def create_spam_check(self, mailbox_id: int) -> Any:
        """`POST /spam-checks/` — sends a test email from the mailbox; free."""
        return self._request(
            "POST", "/spam-checks/",
            json={"mailboxId": self._id("mailbox_id", mailbox_id)})

    def get_spam_check(self, spam_check_id: int) -> Any:
        """`GET /spam-checks/{id}`."""
        return self._request(
            "GET", f"/spam-checks/{self._id('spam_check_id', spam_check_id)}")

    def delete_spam_check(self, spam_check_id: int) -> Any:
        """`DELETE /spam-checks/{id}`."""
        return self._request(
            "DELETE", f"/spam-checks/{self._id('spam_check_id', spam_check_id)}")

    # --- warmup -------------------------------------------------------------

    def list_warmup_inboxes(self, limit: Optional[int] = None,
                            offset: Optional[int] = None) -> Any:
        """`GET /warmup` — inboxes enrolled in warmup."""
        return self._request("GET", "/warmup", params=self._page(limit, offset))

    def get_warmup_inbox(self, warmup_id: int) -> Any:
        """`GET /warmup/{id}` — settings and daily counters."""
        return self._request("GET", f"/warmup/{self._id('warmup_id', warmup_id)}")

    def list_available_warmup_inboxes(self, limit: Optional[int] = None,
                                      offset: Optional[int] = None) -> Any:
        """`GET /warmup/available-inboxes` — active mailboxes not yet enrolled."""
        return self._request("GET", "/warmup/available-inboxes",
                             params=self._page(limit, offset))

    # --- subscription -------------------------------------------------------

    def get_subscription_slots(self) -> Any:
        """`GET /subscriptions/slots` — mailboxes used vs paid, per type."""
        return self._request("GET", "/subscriptions/slots")
