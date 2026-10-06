"""Dropcontact API client — contact + company enrichment (email/phone/SIRENE).

Async bulk API: POST a batch (max 250 contacts, ≤15 kB per contact) → GET the
result by `request_id` once processing is done (typically ~30s+, no fixed SLA
documented). Same submit/fetch split as FullEnrich (signal #252): `submit`
returns as soon as Dropcontact acks the batch, `fetch` is a single un-cached
status check — polling belongs to the caller, never to this client.

Auth: header `X-Access-Token` (not `Authorization: Bearer`). Credits are
"pay on success" — a POST with a single empty contact (`{"data": [{}]}`) costs
0 credits and returns `credits_left`, used here for `check_credits`.

Requires: requests
"""
from __future__ import annotations

import json

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# Documented batch cap (exceeding it → the API rejects the whole request).
MAX_CONTACTS_PER_BATCH = 250
# "Single contact data must not exceed 15 kB" — checked client-side to avoid
# an HTTP round trip doomed to fail on an oversized item.
MAX_CONTACT_BYTES = 15_000


class DropcontactClient:
    BASE_URL = "https://api.dropcontact.com/v1"

    def __init__(self, api_key: str | None = None):
        self.api_key = require(api_key, "DROPCONTACT_API_KEY")

    def _headers(self) -> dict:
        return {
            "X-Access-Token": self.api_key,
            "Content-Type": "application/json",
        }

    def submit(
        self,
        contacts: list[dict],
        *,
        siren: bool = False,
        language: str | None = None,
        custom_callback_url: str | None = None,
    ) -> dict:
        """Submits an enrichment batch. Returns Dropcontact's acknowledgement
        immediately (`request_id`, `credits_left`, and the per-item echo of
        `data` — each item may carry its own validation `errors`/`warnings`,
        Dropcontact processes the rest of the batch despite an invalid item).
        The job runs on Dropcontact's side; collect it via `fetch(request_id)`.

        contacts: 1-250 objects. Each must carry enough to identify a contact
        (email, OR linkedin, OR first_name+last_name+company, OR full_name+company)
        — Dropcontact still processes an incomplete item but reports it in
        `errors`/`warnings` in the response rather than failing the batch.
        Recognized fields per item: email, first_name, last_name, full_name, phone,
        company, website, num_siren, siret, linkedin, company_linkedin, country,
        job, custom_fields (preserved as-is in the result).
        """
        if not contacts:
            raise ValueError("Dropcontact submit: no contact provided.")
        if len(contacts) > MAX_CONTACTS_PER_BATCH:
            raise ValueError(
                f"Dropcontact submit: {len(contacts)} contacts > limit "
                f"{MAX_CONTACTS_PER_BATCH}/request — split into several calls."
            )
        for i, c in enumerate(contacts):
            size = len(json.dumps(c, ensure_ascii=False).encode("utf-8"))
            if size > MAX_CONTACT_BYTES:
                raise ValueError(
                    f"Dropcontact submit: contact #{i} weighs {size} bytes > "
                    f"limit {MAX_CONTACT_BYTES} bytes/contact."
                )

        payload: dict = {"data": contacts}
        if siren:
            payload["siren"] = True
        if language:
            payload["language"] = language
        if custom_callback_url:
            payload["custom_callback_url"] = custom_callback_url

        resp = requests.post(
            f"{self.BASE_URL}/enrich/all",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        raise_for_upstream(resp, service="dropcontact")
        body = resp.json()
        if not body.get("request_id"):
            raise RuntimeError(f"Dropcontact POST: no request_id in the response: {resp.text[:200]}")
        return body

    # Substring of the ONLY documented "pending" label ("Request not ready yet,
    # try again in 30 seconds") — the behaviour of `reason` for an unknown/expired
    # request_id is NOT documented (could be a 404, handled by
    # `raise_for_upstream`, or some other text here). Never tell the caller
    # to retry on a `reason` we don't recognize.
    _PENDING_MARKER = "not ready"

    def fetch(self, request_id: str, *, force_results: bool = False) -> dict:
        """A single status GET, no waiting. While processing is not finished,
        Dropcontact answers 200 with `success: false` (NOT an HTTP error) — we
        translate that into `{"done": False, "pending": <bool>, "reason": <str>}` (`pending`
        distinguishes the ONLY documented "not ready yet" case from an unknown `reason`, which
        the caller must not treat as "retry later"). Once finished:
        `{"done": True, "data": [...], "credits_left": <int>}`.

        force_results: returns partial results (items not yet processed
        left as-is) instead of waiting for the whole batch to finish.
        """
        params = {"forceResults": "true"} if force_results else None
        resp = requests.get(
            f"{self.BASE_URL}/enrich/all/{request_id}",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        raise_for_upstream(resp, service="dropcontact")
        body = resp.json()

        if not body.get("success"):
            reason = body.get("reason", "")
            return {
                "done": False,
                "pending": self._PENDING_MARKER in reason.lower(),
                "reason": reason,
            }

        return {
            "done": True,
            "data": body.get("data", []),
            "credits_left": body.get("credits_left"),
        }

    def check_credits(self) -> dict:
        """0-credit probe (POST with an empty contact) — authenticates the key and
        returns `credits_left` without consuming quota."""
        return self.submit([{}])
