"""WordPress REST API client (core `wp/v2` + any namespace the site exposes).

Auth = **Application Password** (WordPress ≥ 5.6), sent as HTTP Basic over
HTTPS — the only built-in way for a third party to call the REST API without
a plugin. The credential is three values: the site URL, the WordPress
username, and the application password (spaces are part of the display format
and are accepted as-is by WordPress).

The API root is DISCOVERED, never assumed: `/wp-json/` on sites with pretty
permalinks, `/?rest_route=/` on the others (both are core, same routes). The
first call probes `/wp-json/` and falls back to `?rest_route=` when it does not
answer the REST index — cached for the client's lifetime.

Redirects are NOT followed (`allow_redirects=False`): `requests` drops the
Authorization header on a cross-host redirect (the call would come back as an
anonymous 401 that blames the password), and an upstream redirect is also the
classic way to turn a vetted public URL into a request to an internal host.
A 3xx is raised as an actionable error naming the target — the user fixes the
site URL once (http→https, www, a moved domain).

Pagination headers (`X-WP-Total`, `X-WP-TotalPages`) carry the totals, so the
list call returns `{"items", "total", "total_pages"}` rather than a bare list.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..common.errors import UpstreamHTTPError

_HTTP_TIMEOUT = (10, 60)  # (connexion, lecture) — jamais d'attente illimitée
_USER_AGENT = "oto-wordpress/1 (+https://oto.cx)"


class WordPressRedirect(ValueError):
    """The site answered with a redirect — the stored site URL is not the one
    WordPress serves. Carries the target so the caller can say which URL to use."""

    def __init__(self, location: str):
        self.location = location
        super().__init__(
            f"le site WordPress redirige vers {location or '(cible absente)'} — "
            "enregistre plutôt cette adresse comme URL du site (https, avec ou "
            "sans www selon ce que le site sert).")


def normalize_site_url(site_url: str) -> str:
    """`https://example.com/blog` form: scheme kept (defaults to https), no
    trailing slash, no query/fragment. A WordPress installed in a sub-directory
    keeps its path — the REST root lives under it."""
    raw = (site_url or "").strip()
    if not raw:
        raise ValueError("URL du site WordPress manquante.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"URL de site WordPress invalide : {site_url!r}")
    path = parts.path.rstrip("/")
    # Pasting the admin or the API URL is common — keep the site root.
    for suffix in ("/wp-admin", "/wp-json", "/wp-login.php"):
        idx = path.find(suffix)
        if idx != -1:
            path = path[:idx]
    return f"{parts.scheme}://{parts.netloc}{path}"


class WordPressClient:
    def __init__(self, site_url: str, username: str, application_password: str,
                 *, rest_mode: Optional[str] = None):
        self.site_url = normalize_site_url(site_url)
        self._auth = (require(username, "WORDPRESS_USERNAME").strip(),
                      require(application_password, "WORDPRESS_APPLICATION_PASSWORD").strip())
        # "pretty" (/wp-json/<route>) | "query" (/?rest_route=/<route>) — probed lazily.
        self._rest_mode = rest_mode

    # --- transport -------------------------------------------------------------

    def _url(self, route: str, mode: str) -> Tuple[str, Dict[str, str]]:
        route = route.strip("/")
        if mode == "pretty":
            return f"{self.site_url}/wp-json/{route}", {}
        return f"{self.site_url}/", {"rest_route": f"/{route}"}

    @property
    def rest_mode(self) -> str:
        if self._rest_mode is None:
            self._rest_mode = self._probe_rest_mode()
        return self._rest_mode

    def _probe_rest_mode(self) -> str:
        """`/wp-json/` if it answers the REST index, else `?rest_route=`. The
        probe is unauthenticated on purpose: the index is public, and a wrong
        mode must not be mistaken for a wrong password.

        A redirect on `/wp-json/` alone is not conclusive (some hosts rewrite
        that path while `?rest_route=` answers directly): it is raised only if
        the other form does not answer either."""
        redirect: Optional[str] = None
        for mode in ("pretty", "query"):
            url, params = self._url("", mode)
            resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT,
                                allow_redirects=False,
                                headers={"User-Agent": _USER_AGENT,
                                         "Accept": "application/json"})
            if resp.status_code in (301, 302, 303, 307, 308):
                redirect = redirect or resp.headers.get("Location", "")
                continue
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    continue
                if isinstance(body, dict) and "namespaces" in body:
                    return mode
        if redirect is not None:
            raise WordPressRedirect(redirect)
        raise ValueError(
            f"{self.site_url} ne répond pas comme un site WordPress : ni "
            "/wp-json/ ni ?rest_route=/ ne servent l'API REST. Vérifie l'URL, ou "
            "qu'une extension de sécurité ne désactive pas l'API REST.")

    def request(self, method: str, route: str, *, params: Optional[dict] = None,
                json: Any = None, data: Optional[bytes] = None,
                headers: Optional[dict] = None) -> Tuple[Any, Dict[str, str]]:
        """One REST call → `(parsed body, response headers)`. `route` is the path
        after the REST root, namespace included (`wp/v2/posts`)."""
        url, base_params = self._url(route, self.rest_mode)
        all_params = {**base_params, **(params or {})}
        hdrs = {"User-Agent": _USER_AGENT, "Accept": "application/json",
                **(headers or {})}
        for attempt in range(3):
            resp = requests.request(
                method, url, params=all_params, json=json, data=data,
                headers=hdrs, auth=self._auth, timeout=_HTTP_TIMEOUT,
                allow_redirects=False)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(min(int(resp.headers.get("Retry-After", 5) or 5), 30))
                continue
            if resp.status_code in (301, 302, 303, 307, 308):
                raise WordPressRedirect(resp.headers.get("Location", ""))
            raise_for_upstream(resp, service="wordpress")
            if not resp.content:
                return {}, dict(resp.headers)
            try:
                return resp.json(), dict(resp.headers)
            except ValueError:
                # A 200 that is not JSON = a cache/WAF page or a PHP notice
                # printed before the payload. Never pretend it parsed.
                raise UpstreamHTTPError(
                    resp.status_code,
                    f"réponse non-JSON (début : {resp.text[:200]!r})",
                    service="wordpress")
        raise UpstreamHTTPError(429, "rate limit après 3 tentatives", service="wordpress")

    def _get(self, route: str, **params) -> Any:
        return self.request("GET", route, params=params or None)[0]

    # --- site ------------------------------------------------------------------

    def index(self) -> Dict:
        """The REST index: name, description, url, `namespaces` (what plugins are
        installed: `yoast/v1`, `rankmath/v1`, `wc/v3`, `acf/v3`…), and
        `authentication` (application-passwords authorization endpoint)."""
        return self._get("")

    def public_index(self) -> Dict:
        """The REST index fetched WITHOUT credentials. What a caller needs
        before it has a working password (the application-passwords
        authorization endpoint): WordPress checks Basic credentials on EVERY
        REST route, the public index included, so a placeholder login would
        turn this public read into a 401."""
        url, params = self._url("", self.rest_mode)
        resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT,
                            allow_redirects=False,
                            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        if resp.status_code in (301, 302, 303, 307, 308):
            raise WordPressRedirect(resp.headers.get("Location", ""))
        raise_for_upstream(resp, service="wordpress")
        try:
            return resp.json()
        except ValueError:
            raise UpstreamHTTPError(resp.status_code, "réponse non-JSON", service="wordpress")

    def me(self) -> Dict:
        """The authenticated user, `context=edit` so `roles` and `capabilities`
        come back — the only way to know what this password is allowed to do."""
        return self._get("wp/v2/users/me", context="edit")

    def settings(self) -> Dict:
        """Site settings (title, timezone, default category…). Admin only (403
        otherwise) — callers treat a 403 as "not available", not as an error."""
        return self._get("wp/v2/settings")

    def types(self) -> Dict:
        """Post types keyed by slug, with `rest_base` and `rest_namespace`."""
        return self._get("wp/v2/types", context="edit")

    def taxonomies(self) -> Dict:
        return self._get("wp/v2/taxonomies", context="edit")

    # --- generic collection CRUD (posts, pages, CPTs, terms, media) --------------

    def list(self, route: str, *, page: int = 1, per_page: int = 20,
             **params) -> Dict:
        """One page of a collection. `per_page` is capped at 100 by WordPress."""
        q = {"page": page, "per_page": max(1, min(per_page, 100)),
             **{k: v for k, v in params.items() if v is not None}}
        body, headers = self.request("GET", route, params=q)
        lower = {k.lower(): v for k, v in headers.items()}
        def _int(name):
            try:
                return int(lower.get(name))
            except (TypeError, ValueError):
                return None
        return {"items": body if isinstance(body, list) else [],
                "total": _int("x-wp-total"),
                "total_pages": _int("x-wp-totalpages"),
                "page": page}

    def get(self, route: str, item_id: int, *, context: str = "edit") -> Dict:
        return self._get(f"{route}/{item_id}", context=context)

    def create(self, route: str, body: Dict) -> Dict:
        return self.request("POST", route, json=body)[0]

    def update(self, route: str, item_id: int, body: Dict) -> Dict:
        return self.request("POST", f"{route}/{item_id}", json=body)[0]

    def delete(self, route: str, item_id: int, *, force: bool = False) -> Dict:
        """`force=False` moves a post to the trash (restorable). Terms and media
        do not support trashing — WordPress refuses them without `force=True`
        (`rest_trash_not_supported`), which the caller must ask for explicitly."""
        params = {"force": "true"} if force else None
        return self.request("DELETE", f"{route}/{item_id}", params=params)[0]

    # --- media -------------------------------------------------------------------

    def upload_media(self, content: bytes, filename: str, mime_type: str,
                     **fields) -> Dict:
        """Raw-body upload (`Content-Disposition` carries the filename), then the
        optional fields (`alt_text`, `caption`, `title`, `description`, `post`)
        in a second call — the raw-body form cannot carry them."""
        safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "") or "upload"
        media, _ = self.request(
            "POST", "wp/v2/media", data=content,
            headers={"Content-Type": mime_type or "application/octet-stream",
                     "Content-Disposition": f'attachment; filename="{safe_name}"'})
        extra = {k: v for k, v in fields.items() if v is not None}
        if extra and media.get("id"):
            media = self.update("wp/v2/media", media["id"], extra)
        return media
