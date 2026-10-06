"""
FullEnrich API Client — waterfall multi-provider contact enrichment.

Async bulk API: POST job → GET status (FINISHED after ~30s-4min).
Pricing: 10 cr/phone, 1 cr/work_email, 3 cr/personal_email (pay-per-result).
Phone hit rate ~70% vs Kaspr ~13%.

⚠️ Async surface by design (signal #252, 2026-07-22): the old synchronous
`enrich_linkedin` polled in-process (131-147s measured) → every MCP client hangs up before
(~60s), result lost AND credits consumed. The `submit`/`fetch` pair keeps each
call short; POLLING belongs to the CALLER (agent), not the HTTP client.

Requires: requests
"""

from __future__ import annotations

import time

import requests

from ..common.credentials import require

# Cap of the FullEnrich bulk endpoint (contacts per job).
MAX_CONTACTS_PER_JOB = 100

DEFAULT_ENRICH_FIELDS = ["contact.work_emails", "contact.phones"]

# Status NAMED by this client (not an upstream status): the job does not exist or no longer
# exists at FullEnrich (`404 error.enrichment.not_found`).
STATUS_NOT_FOUND = "NOT_FOUND"

# Documented HTTP ERROR responses of `GET /contact/enrich/bulk/{id}` that actually
# express a job STATUS (API v2 doc). `400 error.enrichment.in_progress` is the
# NORMAL response of a job that is not ready yet: raised as an error, it made
# every poll fail until the job finished (signals #1027-#1029). Classified on the HTTP
# code AND the body's `code`, never on the message text.
_GET_ERREUR_VERS_STATUT = {
    (400, "error.enrichment.in_progress"): "IN_PROGRESS",
    (404, "error.enrichment.not_found"): STATUS_NOT_FOUND,
    (429, "error.rate.limit"): "RATE_LIMIT",
}

_CREDITS_INSUFFISANTS = "FullEnrich: insufficient credits. Top up at app.fullenrich.com."


def _cost_credits(body) -> int | None:
    """`cost.credits` of a job result, if it is an integer `>= 0` — otherwise `None`.

    `bool` is explicitly excluded (`True` is an `int` in Python): a flag is not a
    number of credits, and taking it for `1` would invent a consumption."""
    cost = body.get("cost") if isinstance(body, dict) else None
    credits = cost.get("credits") if isinstance(cost, dict) else None
    if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
        return None
    return credits


class FullenrichProfile:
    """Parsed enrichment result for 1 LinkedIn profile."""

    def __init__(
        self,
        linkedin_slug: str | None,
        first_name: str | None = None,
        last_name: str | None = None,
        full_name: str | None = None,
        title: str | None = None,
        company_name: str | None = None,
        phones: list[str] | None = None,
        work_emails: list[str] | None = None,
        personal_emails: list[str] | None = None,
        location: str | None = None,
        raw_data: dict | None = None,
        fetched_at: str | None = None,
    ):
        self.linkedin_slug = linkedin_slug
        self.first_name = first_name
        self.last_name = last_name
        self.full_name = full_name
        self.title = title
        self.company_name = company_name
        self.phones = phones or []
        self.work_emails = work_emails or []
        self.personal_emails = personal_emails or []
        self.location = location
        self.raw_data = raw_data
        self.fetched_at = fetched_at

    @property
    def found(self) -> bool:
        return bool(self.phones or self.work_emails or self.personal_emails)

    def to_dict(self) -> dict:
        return {
            "found": self.found,
            "linkedin_slug": self.linkedin_slug,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "title": self.title,
            "company_name": self.company_name,
            "phones": self.phones,
            "work_emails": self.work_emails,
            "personal_emails": self.personal_emails,
            "location": self.location,
            "fetched_at": self.fetched_at,
        }


class FullenrichClient:
    BASE_URL = "https://app.fullenrich.com/api/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = require(api_key, "FULLENRICH_API_KEY")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def submit(
        self,
        contacts: list[dict],
        enrich_fields: list[str] | None = None,
    ) -> str:
        """Submit a bulk enrichment job. Returns the enrichment_id (the job
        runs on the FullEnrich side, ~30s-4min; retrieve via `fetch`).

        contacts: [{first_name, last_name, linkedin_slug?, company_name?, domain?}, ...]
        Each contact must carry `linkedin_slug` OR `domain` (the company's
        website) — otherwise the FullEnrich API rejects the entire job
        (`error.enrichment.domain.empty`, verified live 2026-07-22).
        """
        if not contacts:
            raise ValueError("FullEnrich submit: no contact provided.")
        if len(contacts) > MAX_CONTACTS_PER_JOB:
            raise ValueError(
                f"FullEnrich submit: {len(contacts)} contacts > cap "
                f"{MAX_CONTACTS_PER_JOB}/job — split into several jobs."
            )
        fields = enrich_fields or DEFAULT_ENRICH_FIELDS

        data = []
        for c in contacts:
            first_name, last_name = c.get("first_name"), c.get("last_name")
            if not first_name or not last_name:
                raise ValueError(
                    f"FullEnrich submit: first_name and last_name required per contact (received {c!r})."
                )
            entry: dict = {
                "first_name": first_name,
                "last_name": last_name,
                "enrich_fields": fields,
            }
            slug = (c.get("linkedin_slug") or "").strip().strip("/")
            if slug:
                entry["linkedin_url"] = f"https://www.linkedin.com/in/{slug}/"
                entry["custom"] = {"slug": slug}
            if c.get("domain"):
                entry["domain"] = c["domain"]
            if not slug and not c.get("domain"):
                raise ValueError(
                    f"FullEnrich submit: linkedin_slug OR domain required per contact "
                    f"(the API otherwise rejects the entire job; received {c!r})."
                )
            if c.get("company_name"):
                entry["company_name"] = c["company_name"]
            data.append(entry)

        payload = {"name": f"oto-{int(time.time())}", "data": data}
        resp = requests.post(
            f"{self.BASE_URL}/contact/enrich/bulk",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"FullEnrich POST {resp.status_code}: {resp.text[:200]}")

        enrichment_id = resp.json().get("enrichment_id")
        if not enrichment_id:
            raise RuntimeError(f"FullEnrich POST: no enrichment_id in response: {resp.text[:200]}")
        return enrichment_id

    def fetch(self, enrichment_id: str) -> dict:
        """A single status GET, without waiting. Returns
        `{"status": <str>, "profiles": [FullenrichProfile] | None, "cost_credits": int | None}` —
        `profiles` is only populated if status == FINISHED. `status` is also
        `IN_PROGRESS` on a `400 error.enrichment.in_progress`, `RATE_LIMIT` on a
        `429`, and `NOT_FOUND` (`STATUS_NOT_FOUND`) on a `404`: these are upstream HTTP
        error responses that express a job status, not a failure.

        `cost_credits` = the credits that FULLENRICH deducted for this job, as
        the upstream declares them (`cost.credits` of the result, aggregated over the whole job — no
        per-contact detail). Read from the response, never recomputed here: the rate card
        (1 / 3 / 10 per value found) is the upstream's and may change without
        notice. `None` when the response does not carry an integer `>= 0`, whatever
        the status."""
        resp = requests.get(
            f"{self.BASE_URL}/contact/enrich/bulk/{enrichment_id}",
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            try:
                err = resp.json()
            except ValueError:
                err = None
            code = err.get("code") if isinstance(err, dict) else None
            statut = _GET_ERREUR_VERS_STATUT.get((resp.status_code, code))
            if statut is not None:
                return {"status": statut, "profiles": None, "cost_credits": None}
            if resp.status_code == 402:
                raise RuntimeError(_CREDITS_INSUFFISANTS)
            raise RuntimeError(f"FullEnrich GET {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        status = body.get("status", "")
        cost_credits = _cost_credits(body)

        if status == "CREDITS_INSUFFICIENT":
            raise RuntimeError(_CREDITS_INSUFFISANTS)

        if status != "FINISHED":
            return {"status": status, "profiles": None, "cost_credits": cost_credits}

        profiles = [self._parse(item) for item in body.get("data", [])]
        return {"status": status, "profiles": profiles, "cost_credits": cost_credits}

    def _parse(self, item: dict) -> FullenrichProfile:
        contact = item.get("contact_info") or {}
        profile = item.get("profile") or {}
        employment = (profile.get("employment") or {}).get("all") or []
        loc = profile.get("location") or {}
        slug = (item.get("custom") or {}).get("slug")

        phones = [p["number"] for p in (contact.get("phones") or []) if p.get("number")]
        work_emails = [e["email"] for e in (contact.get("work_emails") or []) if e.get("email")]
        personal_emails = [e["email"] for e in (contact.get("personal_emails") or []) if e.get("email")]

        title = employment[0].get("title") if employment else None
        company = employment[0].get("company", {}).get("name") if employment else None
        location_parts = [loc.get("city"), loc.get("country")]
        location_str = ", ".join(p for p in location_parts if p) or None

        from datetime import datetime, timezone

        return FullenrichProfile(
            linkedin_slug=slug,
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
            full_name=profile.get("full_name"),
            title=title,
            company_name=company,
            phones=phones,
            work_emails=work_emails,
            personal_emails=personal_emails,
            location=location_str,
            raw_data=item,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
