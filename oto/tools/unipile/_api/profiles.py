"""Member & company profiles, with the #153 anti-mismatch guard.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..const import _SCRAPE_TIMEOUT, _sections_param, _slug_from_company_url
from ..errors import UnipileError


class _ProfilesMixin:
    """Member & company profiles, with the #153 anti-mismatch guard."""

    @staticmethod
    def _identity_ok(requested: str, resp: dict, expect_object: str) -> bool:
        """True if `resp` matches both the requested type AND identifier.
        Tolerates slug↔id (compares requested to `public_identifier`, `id`,
        `provider_id`, case-insensitive)."""
        if not isinstance(resp, dict):
            return False
        obj = resp.get("object")
        if obj and expect_object and obj != expect_object:
            return False  # e.g. requested UserProfile, received CompanyProfile (#148/#149)
        req = str(requested).strip().lower()
        cands = {
            str(resp.get(k, "")).strip().lower()
            for k in ("public_identifier", "id", "provider_id", "member_urn")
        }
        return req in cands if any(cands) else True  # no id to compare → let it through

    def get_profile(self, identifier: str, sections: str = "*") -> dict:
        """Full profile. `identifier` = public identifier (slug) or provider id.

        #153 guard: rejects a response that doesn't match the requested member
        (wrong pairing observed under concurrency) → retryable `UnipileError`."""
        params: dict[str, Any] = {}
        secs = _sections_param(sections)
        if secs:
            params["with_sections"] = secs
        data = self._request(
            "GET", self._acct(f"/users/{quote(identifier, safe='')}"), params=params
        )
        if not self._identity_ok(identifier, data, "UserProfile"):
            got = (data or {}).get("public_identifier") or (data or {}).get("id")
            raise UnipileError(
                f"Unipile identity_mismatch: requested profile {identifier!r}, "
                f"received {got!r} (object={(data or {}).get('object')!r}). "
                "Response rejected — retry."
            )
        return data

    def _get_company_raw(self, identifier: str) -> dict:
        """Raw company GET + #153 anti-mismatch guard. Raises as is
        (404 included) — the resolution fallback lives in `get_company`."""
        data = self._request(
            "GET", self._acct(f"/linkedin/company/{quote(identifier, safe='')}"),
            timeout=_SCRAPE_TIMEOUT,
        )
        if not self._identity_ok(identifier, data, "CompanyProfile"):
            got = (data or {}).get("public_identifier") or (data or {}).get("id")
            raise UnipileError(
                f"Unipile identity_mismatch: requested company {identifier!r}, "
                f"received {got!r} (object={(data or {}).get('object')!r}). "
                "Response rejected — retry."
            )
        return data

    def _resolve_company_slugs(self, name: str, limit: int = 5) -> list[str]:
        """#176: search companies by name → candidate `public_identifier`s,
        in order of relevance. Best-effort: must never mask the original
        404 (any search error → no candidates)."""
        try:
            res = self.search(category="companies", keywords=name)
        except Exception:  # noqa: BLE001 — best-effort resolution, never fatal
            return []
        items = (res or {}).get("items") or (res or {}).get("data") or []
        out: list[str] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            slug = it.get("public_identifier") or _slug_from_company_url(
                it.get("public_profile_url") or it.get("profile_url") or ""
            )
            if slug:
                out.append(slug)
        return list(dict.fromkeys(out))  # dedup preserving order

    def get_company(self, identifier: str, resolve: bool = True) -> dict:
        """Company page. `identifier` = slug (`public_identifier`) or numeric id.

        #153 guard: rejects a response for another object/identifier.
        Tolerant resolution #176: if the provided slug is not found (404) and
        is not numeric, we try a company search by name to find the canonical
        `public_identifier` (e.g. `mooniz` → `mooniz1`) and retry.
        On failure → clean 404 enriched with close candidates (`resolve=False` turns
        off the fallback)."""
        try:
            return self._get_company_raw(identifier)
        except UnipileError as e:
            ident = str(identifier).strip()
            if not resolve or e.status_code != 404 or ident.isdigit():
                raise
            candidates = self._resolve_company_slugs(ident)
            for slug in candidates:
                if slug.strip().lower() != ident.lower():
                    try:
                        return self._get_company_raw(slug)
                    except UnipileError:
                        continue
            if candidates:
                raise UnipileError(
                    f"Unipile 404: company {identifier!r} not found. "
                    f"Close candidate slugs: {', '.join(candidates)}.",
                    status_code=404,
                ) from e
            raise
