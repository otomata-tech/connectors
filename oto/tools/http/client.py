"""Client of the generic `http` connector — multi-method, multi-auth HTTP call.

A simple "HTTP node" (like n8n's HTTP Request node / a Zapier action):
injects the configured auth mode (bearer / key in header or query / basic /
oauth2 client-credentials) and forwards the requested method (GET by default; POST /
PUT / PATCH / DELETE with a JSON body) to the target API.

SSRF protection does NOT live here: as in market products (Zapier,
Make, n8n, GPT Actions…), outbound traffic initiated by a tenant is filtered at
the platform's **network/egress** level, not by per-connector code.

Pure (`requests` only) — credential resolution and translation into MCP errors
live in the `oto_mcp/tools/http.py` adapter.
"""
from __future__ import annotations

import threading
import time
from abc import ABC
from urllib.parse import urlsplit

import requests
from requests.auth import HTTPBasicAuth

AUTH_MODES = ("bearer", "header", "query", "basic", "oauth2", "none")


# --- auth modes ----------------------------------------------------------------

class UpstreamAuth(ABC):
    """Interface for injecting auth into outbound requests."""

    def configure(self, session: requests.Session) -> None:  # noqa: B027
        """Apply the auth to a fresh session. No-op by default."""

    def query_params(self) -> dict:
        return {}

    def refresh(self, session: requests.Session) -> None:  # noqa: B027
        """Re-authenticate after a 401. No-op by default (static credential)."""


class NoAuth(UpstreamAuth):
    """No authentication (public API)."""


class StaticBearer(UpstreamAuth):
    def __init__(self, token: str):
        if not token:
            raise ValueError("StaticBearer: empty token")
        self._token = token

    def configure(self, session: requests.Session) -> None:
        session.headers["Authorization"] = f"Bearer {self._token}"


class ApiKeyHeader(UpstreamAuth):
    def __init__(self, name: str, value: str):
        if not name or not value:
            raise ValueError("ApiKeyHeader: name/value required")
        self._name, self._value = name, value

    def configure(self, session: requests.Session) -> None:
        session.headers[self._name] = self._value


class ApiKeyQuery(UpstreamAuth):
    def __init__(self, param: str, value: str):
        if not param or not value:
            raise ValueError("ApiKeyQuery: param/value required")
        self._param, self._value = param, value

    def query_params(self) -> dict:
        return {self._param: self._value}


class BasicAuth(UpstreamAuth):
    def __init__(self, username: str, password: str):
        if not username:
            raise ValueError("BasicAuth: username required")
        self._auth = HTTPBasicAuth(username, password)

    def configure(self, session: requests.Session) -> None:
        session.auth = self._auth


class OAuth2ClientCredentials(UpstreamAuth):
    """OAuth2 client-credentials: fetch an access_token + TTL cache."""

    def __init__(self, token_url: str, client_id: str, client_secret: str,
                 scope: str | None = None, timeout: int = 30, leeway: int = 30):
        if not token_url or not client_id or not client_secret:
            raise ValueError("OAuth2ClientCredentials: token_url/client_id/client_secret required")
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._timeout = timeout
        self._leeway = leeway
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expiry = 0.0

    def _fetch(self) -> str:
        data = {"grant_type": "client_credentials",
                "client_id": self._client_id, "client_secret": self._client_secret}
        if self._scope:
            data["scope"] = self._scope
        r = requests.post(self._token_url, data=data, timeout=self._timeout)
        r.raise_for_status()
        payload = r.json()
        token = payload.get("access_token")
        if not token:
            raise ValueError("OAuth2ClientCredentials: response without access_token")
        self._token = token
        self._expiry = time.monotonic() + int(payload.get("expires_in", 3600)) - self._leeway
        return token

    def _current(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() >= self._expiry:
                return self._fetch()
            return self._token

    def configure(self, session: requests.Session) -> None:
        session.headers["Authorization"] = f"Bearer {self._current()}"

    def refresh(self, session: requests.Session) -> None:
        with self._lock:
            self._fetch()
        session.headers["Authorization"] = f"Bearer {self._token}"


def build_auth(mode: str, fields: dict) -> UpstreamAuth:
    """Build the `UpstreamAuth` for an `auth_mode` + the credential fields.

    Raises `ValueError` (actionable message) if the mode is unknown or a
    required field is missing."""
    mode = (mode or "").strip().lower()

    def val(key: str) -> str:
        return (fields.get(key) or "").strip()

    def need(*keys: str) -> None:
        missing = [k for k in keys if not val(k)]
        if missing:
            raise ValueError(f"auth_mode={mode!r} requires: {', '.join(missing)}")

    if mode == "bearer":
        need("token"); return StaticBearer(val("token"))
    if mode == "header":
        need("header_name", "token"); return ApiKeyHeader(val("header_name"), val("token"))
    if mode == "query":
        need("query_param", "token"); return ApiKeyQuery(val("query_param"), val("token"))
    if mode == "basic":
        need("username", "password"); return BasicAuth(val("username"), fields.get("password") or "")
    if mode == "oauth2":
        need("token_url", "client_id", "client_secret")
        return OAuth2ClientCredentials(val("token_url"), val("client_id"),
                                       val("client_secret"), scope=val("scope") or None)
    if mode == "none":
        return NoAuth()
    raise ValueError(f"unknown auth_mode: {mode!r} (expected: {'|'.join(AUTH_MODES)})")


# --- forward -------------------------------------------------------------------

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class HttpConnectorClient:
    """HTTP node: (base_url, auth_mode, fields) → `.request(method, path, …)`.

    Injects the auth, forwards the method (GET/POST/PUT/PATCH/DELETE), single retry
    after re-auth on 401. `.get()`/`.post()` = shortcuts. Raises `ValueError` on
    invalid config (non-http(s) scheme, invalid mode/field/method).

    Like n8n/Make's HTTP node: write methods are carried through;
    responsibility for what they do lies with the target API (and, for a
    downstream bridge, with ITS own allowlist)."""

    def __init__(self, base_url: str, auth_mode: str, fields: dict, *, timeout: int = 45):
        base_url = (base_url or "").strip().rstrip("/")
        if urlsplit(base_url).scheme not in ("http", "https"):
            raise ValueError("base_url must be http(s)")
        self._base_url = base_url
        self._auth = build_auth(auth_mode, fields)
        self._timeout = timeout
        self._session: requests.Session | None = None

    def _ready(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            self._auth.configure(s)
            self._session = s
        return self._session

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | list | None = None,
    ) -> dict:
        method = (method or "").strip().upper()
        if method not in METHODS:
            raise ValueError(f"invalid method: {method!r} (expected: {'|'.join(METHODS)})")
        if not path.startswith("/"):
            raise ValueError("path must start with / (relative to base_url)")
        s = self._ready()
        url = self._base_url + path

        def _send():
            merged = {**(params or {}), **self._auth.query_params()}
            return s.request(method, url, params=merged or None, json=json,
                             timeout=self._timeout)

        r = _send()
        if r.status_code == 401:
            self._auth.refresh(s)
            r = _send()
        r.raise_for_status()
        return r.json() if r.content else {}

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params=params)

    def get_raw(self, path: str, params: dict | None = None, *,
                max_bytes: int) -> tuple[bytes, str]:
        """GET `path` and return the body's BYTES and its content type, read as a
        stream and bounded by `max_bytes` — for a body that is a file (a CSV, a
        PDF), not JSON. Same auth and single re-auth on 401 as `request`.

        A redirect is refused, not followed: the auth this client injects would
        leave with it, and a 3xx is the classic way around an egress guard. A body
        above `max_bytes` raises before being read whole. An upstream 4xx/5xx raises
        `requests.HTTPError`, like `request`."""
        if not path.startswith("/"):
            raise ValueError("path must start with / (relative to base_url)")
        s = self._ready()
        url = self._base_url + path

        def _send():
            merged = {**(params or {}), **self._auth.query_params()}
            return s.get(url, params=merged or None, timeout=self._timeout,
                         stream=True, allow_redirects=False)

        r = _send()
        if r.status_code == 401:
            r.close()
            self._auth.refresh(s)
            r = _send()
        try:
            if 300 <= r.status_code < 400:
                raise ValueError(f"redirect refused ({r.status_code} to "
                                 f"{r.headers.get('Location') or '?'})")
            r.raise_for_status()
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError(f"response of {declared} bytes > limit {max_bytes}")
            data = bytearray()
            for chunk in r.iter_content(64 * 1024):
                data += chunk
                if len(data) > max_bytes:
                    raise ValueError(f"response > limit {max_bytes} bytes")
            return bytes(data), r.headers.get("Content-Type") or ""
        finally:
            r.close()

    def post(self, path: str, json: dict | list | None = None,
             params: dict | None = None) -> dict:
        return self.request("POST", path, params=params, json=json)
