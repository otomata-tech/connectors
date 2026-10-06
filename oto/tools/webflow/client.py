"""Webflow Data API v2 Client — https://developers.webflow.com/data/reference

Scope: CMS (site/collections/items/publish), webhooks, forms/submissions,
and pages (metadata + read-only static content + site-level publish). No
assets, ecommerce, comments, or custom code — custom code injects arbitrary
JS into every visitor's browser (site- or page-wide), a categorically
different blast radius than data CRUD; deliberately left out until there's
a concrete need.

Auth = Webflow **Site API token** (generated per-site in Site Settings →
Apps & Integrations → API access, scoped with `cms:read`/`cms:write`/
`sites:read`). Site tokens are bound to exactly one site (confirmed against
developers.webflow.com/data/v2.0.0/reference/authentication/site-token —
"Site tokens are created per site"), so the caller never needs to know or
paste a `site_id`: it's resolved lazily on first use via `GET /sites`
(requires the `sites:read` scope) and cached — that call returns exactly one
site for a genuine Site token. An explicit `site_id` can still be passed to
the constructor to skip that resolution (e.g. tests, or a future token type
that spans sites), but it is optional everywhere.

Webflow's item endpoints natively support batching (an `items` array in one
HTTP call) for create/update/delete — unlike e.g. Folk, this client does NOT
need a client-side loop for bulk; `create_items`/`update_items`/`delete_items`
always take a list, and the caller decides whether that list has 1 or N
elements.
"""

import time
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait


class WebflowClient:
    BASE_URL = "https://api.webflow.com/v2"

    def __init__(self, api_key: str = None, site_id: str = None):
        self.api_key = require(api_key, "WEBFLOW_API_KEY")
        self._site_id = site_id

    @property
    def site_id(self) -> str:
        """Resolved lazily via `GET /sites` if not given to the
        constructor, then cached — a Webflow Site API token is bound
        to ONE site, so there is nothing to enter: the token already knows it. Raises
        `ValueError` (not `UpstreamHTTPError`: it is not an HTTP refusal)
        if the token sees zero or several sites — a workspace/OAuth token
        passed here by mistake must never make us guess WHICH of the sites
        it covers is the right one."""
        if self._site_id is None:
            self._site_id = self._resolve_site_id()
        return self._site_id

    def _resolve_site_id(self) -> str:
        sites = self._request("GET", "sites").get("sites", [])
        if len(sites) == 1:
            return sites[0]["id"]
        if not sites:
            raise ValueError(
                "this Webflow token has access to no site — check that it was "
                "generated with the sites:read scope and that it has not been "
                "revoked.")
        raise ValueError(
            f"this Webflow token has access to {len(sites)} sites — expected "
            "exactly 1 for a Site API token (generated from Site "
            "Settings → Apps & Integrations → API access OF the intended site, not "
            f"a workspace token). Sites seen: {[s.get('id') for s in sites]}.")

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Bearer {self.api_key}", "accept": "application/json"}
        if method.upper() not in ("GET", "DELETE"):
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            resp = requests.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kwargs)
            if resp.status_code == 429:
                # Webflow returns Retry-After (usually 60s, see the rate-limits docs).
                wait = int(resp.headers.get("Retry-After", 60))
                time.sleep(wait)
                continue
            raise_for_upstream(resp, service="webflow")
            return resp.json() if resp.content else {}
        raise Exception("Rate limit exceeded after retries")

    # --- Site ---

    def get_site(self) -> Dict:
        return self._request("GET", f"sites/{self.site_id}")

    # --- Collections ---

    def list_collections(self) -> List[Dict]:
        return self._request("GET", f"sites/{self.site_id}/collections").get("collections", [])

    def get_collection(self, collection_id: str) -> Dict:
        """Returns the collection schema (including `fields[]` — needed to
        validate the `fieldData` keys before a create/update)."""
        return self._request("GET", f"collections/{collection_id}")

    # --- Items (staged — draft/unpublished by default) ---

    def list_items(self, collection_id: str, *, offset: int = 0, limit: int = 100,
                    sort_by: Optional[str] = None, sort_order: Optional[str] = None,
                    cms_locale_id: Optional[str] = None, **filters) -> Dict:
        """One page. `filters` = passthrough query params (name, slug, createdOn,
        lastPublished, lastUpdated — with a `[gte]`/`[lte]` suffix on the caller side)."""
        params: Dict[str, Any] = {"offset": offset, "limit": min(limit, 100)}
        if sort_by:
            params["sortBy"] = sort_by
        if sort_order:
            params["sortOrder"] = sort_order
        if cms_locale_id:
            params["cmsLocaleId"] = cms_locale_id
        params.update(filters)
        return self._request("GET", f"collections/{collection_id}/items", params=params)

    def list_all_items(self, collection_id: str, *, cap: int = 500, **kwargs) -> List[Dict]:
        """Paginates `list_items` (page=100) until exhausted or `cap` items —
        prevents an agent call from silently enumerating a collection of 10k
        items at once."""
        items: List[Dict] = []
        offset = 0
        while len(items) < cap:
            page = self.list_items(collection_id, offset=offset, limit=100, **kwargs)
            batch = page.get("items", [])
            if not batch:
                break
            items.extend(batch)
            total = page.get("pagination", {}).get("total", len(items))
            offset += len(batch)
            if offset >= total:
                break
        return items[:cap]

    def get_item(self, collection_id: str, item_id: str) -> Dict:
        return self._request("GET", f"collections/{collection_id}/items/{item_id}")

    def create_items(self, collection_id: str, items: List[Dict]) -> Dict:
        """`items` = list of `{fieldData: {...}, isArchived?, isDraft?, cmsLocaleId?}`."""
        return self._request(
            "POST", f"collections/{collection_id}/items", json={"items": items},
        )

    def update_items(self, collection_id: str, items: List[Dict]) -> Dict:
        """`items` = list of `{id, fieldData?, isArchived?, isDraft?}`."""
        return self._request(
            "PATCH", f"collections/{collection_id}/items", json={"items": items},
        )

    def delete_items(self, collection_id: str, item_ids: List[str]) -> Dict:
        return self._request(
            "DELETE", f"collections/{collection_id}/items",
            json={"items": [{"id": i} for i in item_ids]},
        )

    def publish_items(self, collection_id: str, item_ids: List[str]) -> Dict:
        """Moves STAGED items to LIVE — the only call of this client that
        touches the public site."""
        return self._request(
            "POST", f"collections/{collection_id}/items/publish",
            json={"itemIds": item_ids},
        )

    # --- Webhooks ---
    #
    # ACTUAL API surface (verified live 2026-08-20, not only against the
    # docs): list + create are scoped to the SITE (`/sites/{id}/webhooks`), get +
    # delete are scoped to the WEBHOOK alone (`/webhooks/{id}`, no site_id in
    # the path). NO update/PATCH endpoint exists — reconfiguring a webhook
    # is delete + create. `filter` is accepted ONLY for
    # triggerType="form_submission" (400 `incompatible_webhook_filter` otherwise,
    # confirmed live) — validated client-side to spare the round trip.
    #
    # `secretKey` is returned ONLY AT CREATION (absent from get/list, confirmed
    # live) — use it to verify the `x-webflow-signature` signatures
    # (HMAC-SHA256 of `f"{timestamp}:{body}"`), Webflow never shows it again.

    WEBHOOK_TRIGGER_TYPES = frozenset({
        "form_submission", "site_publish",
        "page_created", "page_metadata_updated", "page_deleted",
        "ecomm_new_order", "ecomm_order_changed", "ecomm_inventory_changed",
        "collection_item_created", "collection_item_changed",
        "collection_item_deleted", "collection_item_published",
        "collection_item_unpublished", "comment_created",
    })

    def list_webhooks(self) -> List[Dict]:
        return self._request(
            "GET", f"sites/{self.site_id}/webhooks").get("webhooks", [])

    def get_webhook(self, webhook_id: str) -> Dict:
        return self._request("GET", f"webhooks/{webhook_id}")

    def create_webhook(self, trigger_type: str, url: str,
                        filter: Optional[Dict] = None) -> Dict:
        """`filter` (form_submission uniquement) = `{"name": "<form name>"}`.
        The response carries `secretKey` in clear text — ONCE only."""
        body: Dict[str, Any] = {"triggerType": trigger_type, "url": url}
        if filter is not None:
            body["filter"] = filter
        return self._request(
            "POST", f"sites/{self.site_id}/webhooks", json=body)

    def delete_webhook(self, webhook_id: str) -> None:
        self._request("DELETE", f"webhooks/{webhook_id}")

    # --- Forms & submissions ---
    #
    # ACTUAL shape of the API (verified against the source docs — `forms/forms/*` and
    # `forms/form-submissions/*`, not `forms/submissions/*` which 404s): listing
    # FORMS is site-scoped (`/sites/{id}/forms`), listing the
    # SUBMISSIONS of ONE form is scoped to both (`/sites/{id}/forms/
    # {form_id}/submissions`), but get/patch/delete OF one submission no
    # longer carry `form_id` in the path (`/sites/{id}/form_submissions/
    # {submission_id}` — the underscore, not the slash, unlike list).
    # NO creation via API — a submission only exists if a visitor
    # fills in the form on the public site.
    #
    # `update_submission` does NOT rewrite the submitted data (the form's
    # content is not editable after the fact): `formSubmissionData` only
    # touches the hidden fields declared in the form's
    # schema — a field not declared as hidden is a silent no-op
    # on the Webflow side, not an error (no client guard is possible without the
    # form schema at hand: go through `get_form` first if the
    # target field must be checked).

    def list_forms(self, *, offset: int = 0, limit: int = 100) -> Dict:
        return self._request(
            "GET", f"sites/{self.site_id}/forms",
            params={"offset": offset, "limit": min(limit, 100)})

    def get_form(self, form_id: str) -> Dict:
        return self._request("GET", f"forms/{form_id}")

    def list_form_submissions(self, form_id: str, *, offset: int = 0,
                               limit: int = 100) -> Dict:
        return self._request(
            "GET", f"sites/{self.site_id}/forms/{form_id}/submissions",
            params={"offset": offset, "limit": min(limit, 100)})

    def get_form_submission(self, submission_id: str) -> Dict:
        return self._request(
            "GET", f"sites/{self.site_id}/form_submissions/{submission_id}")

    def update_form_submission(self, submission_id: str,
                                form_submission_data: Dict) -> Dict:
        """`form_submission_data` touches ONLY the hidden fields declared
        in the form's schema — never the data submitted by the visitor."""
        return self._request(
            "PATCH", f"sites/{self.site_id}/form_submissions/{submission_id}",
            json={"formSubmissionData": form_submission_data})

    def delete_form_submission(self, submission_id: str) -> None:
        self._request(
            "DELETE", f"sites/{self.site_id}/form_submissions/{submission_id}")

    # --- Pages ---
    #
    # Two clearly distinct surfaces, verified against the source docs
    # (`pages-and-components/pages/*` — NOT `pages/*`, which 404s):
    #
    # (1) METADATA (title/slug/seo/openGraph) — read+write, NO
    #     locale restriction. `list` is site-scoped, `get`/`update` are
    #     scoped to the page alone (no site_id in the path).
    #
    # (2) STATIC CONTENT (the text nodes — page titles/paragraphs) —
    #     `get_page_content` (read, `/pages/{id}/dom`) works WITHOUT
    #     restriction (any locale, including the primary/
    #     default one). ⚠️ BUT `update_page_content` (write, same endpoint as
    #     POST) is reserved for SECONDARY locales — confirmed verbatim
    #     against the docs: "This endpoint updates content on a static page in
    #     secondary locales" / "Ensure that the specified localeId is a
    #     valid secondary locale for the site otherwise the request will
    #     fail." On a SINGLE-locale site (no secondary locale
    #     configured — the common case), there is therefore NO API path
    #     to edit the body of a static page: only reading works
    #     fully. `update_page_content` raises `ValueError` if called without
    #     `locale_id` rather than letting an opaque Webflow 400 bubble up —
    #     it is not a forgotten parameter, it is a structural constraint
    #     of the API that must be named.

    def list_pages(self, *, offset: int = 0, limit: int = 100,
                    locale_id: Optional[str] = None) -> Dict:
        params: Dict[str, Any] = {"offset": offset, "limit": min(limit, 100)}
        if locale_id:
            params["localeId"] = locale_id
        return self._request(
            "GET", f"sites/{self.site_id}/pages", params=params)

    def get_page(self, page_id: str) -> Dict:
        """Metadata of ONE page (title/slug/seo/openGraph) — not its content
        (see `get_page_content`)."""
        return self._request("GET", f"pages/{page_id}")

    def update_page(self, page_id: str, *, title: Optional[str] = None,
                     slug: Optional[str] = None, seo: Optional[Dict] = None,
                     open_graph: Optional[Dict] = None,
                     locale_id: Optional[str] = None) -> Dict:
        """Writes ONLY the metadata (title/slug/seo/openGraph) — never
        the page content (see the locale restriction of
        `update_page_content`)."""
        body: Dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if slug is not None:
            body["slug"] = slug
        if seo is not None:
            body["seo"] = seo
        if open_graph is not None:
            body["openGraph"] = open_graph
        params = {"localeId": locale_id} if locale_id else {}
        return self._request(
            "PUT", f"pages/{page_id}", json=body, params=params)

    def get_page_content(self, page_id: str, *, offset: int = 0,
                          limit: int = 100,
                          locale_id: Optional[str] = None) -> Dict:
        """The page's text nodes — READ alone works on any
        locale (including the primary one), unlike writing."""
        params: Dict[str, Any] = {"offset": offset, "limit": min(limit, 100)}
        if locale_id:
            params["localeId"] = locale_id
        return self._request(
            "GET", f"pages/{page_id}/dom", params=params)

    def update_page_content(self, page_id: str, nodes: List[Dict], *,
                             locale_id: str) -> Dict:
        """⚠️ `locale_id` MUST be a SECONDARY locale of the site (not the
        primary one) — a Webflow restriction, not an omission of this client:
        "Ensure that the specified localeId is a valid secondary locale for
        the site otherwise the request will fail." No default value
        is guessed: a site without a configured secondary locale has NO
        way to write a page's content via the API — only reading
        (`get_page_content`) works then.

        `nodes` = list of `{"nodeId": ..., "text": "<html>"}` (or the shape
        specific to the node type — component instance/select/text input/
        submit/search button, see docs)."""
        if not locale_id:
            raise ValueError(
                "update_page_content requires locale_id — Webflow only allows "
                "writing a page's static content on a SECONDARY "
                "locale of the site (never the primary/default one). A "
                "single-locale site (no secondary locale configured) has no "
                "way to edit a page body via the API — only reading "
                "(get_page_content) works then.")
        return self._request(
            "POST", f"pages/{page_id}/dom", json={"nodes": nodes},
            params={"localeId": locale_id})

    def publish_site(self, *, custom_domains: Optional[List[str]] = None,
                      publish_to_webflow_subdomain: bool = False) -> Dict:
        """Publishes the ENTIRE SITE (all pages) — not to be confused with
        `publish_items` (CMS items only). Rate-limited by Webflow to 1
        publish/minute. At least one of the two arguments must designate a
        real target."""
        body: Dict[str, Any] = {}
        if custom_domains:
            body["customDomains"] = custom_domains
        if publish_to_webflow_subdomain:
            body["publishToWebflowSubdomain"] = True
        if not body:
            raise ValueError(
                "publish_site requires custom_domains and/or "
                "publish_to_webflow_subdomain=True — at least one publish "
                "target must be designated.")
        return self._request(
            "POST", f"sites/{self.site_id}/publish", json=body)
