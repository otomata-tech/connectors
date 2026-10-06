"""Zoho CRM API Client — https://www.zoho.com/crm/developer/docs/api/v7/"""

import time
from typing import Callable, Any, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream
from .auth import ZohoAuthError, cred_key, get_access_token, invalidate

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely

__all__ = ["ZohoAuthError", "ZohoClient"]


class ZohoClient:
    API_VERSION = "v7"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        api_domain: Optional[str] = None,
        accounts_url: Optional[str] = None,
        on_refresh: Optional[Callable[[dict], None]] = None,
    ):
        """Initialize the client.

        Credentials are always supplied by the consumer (multi-user server usage:
        each call builds a client with the user's resolved creds). The access
        token is cached **in process memory**, keyed by credential
        (`.auth`) — never on disk, never shared between distinct credentials."""
        self.client_id = require(client_id, "ZOHO_CLIENT_ID")
        self.client_secret = require(client_secret, "ZOHO_CLIENT_SECRET")
        # OPTIONAL at construction: in server-based mode it is obtained through the
        # consent flow, not pasted. Its absence is reported at refresh time
        # (actionable message) rather than as a config error.
        self.refresh_token = refresh_token
        self.api_domain = api_domain or "https://www.zohoapis.com"
        self.accounts_url = accounts_url or "https://accounts.zoho.com"
        self._cred_key = cred_key(
            self.accounts_url, self.client_id, self.refresh_token)
        # Called after every SUCCESSFUL refresh — never on a cache hit.
        # Symmetric to the Salesforce client's `on_refresh`: it is the only moment
        # when the caller learns that this credential really authenticates, now.
        self._on_refresh = on_refresh

    # --- Auth ---

    def _get_access_token(self) -> str:
        """Valid access token, refreshed as needed. PROCESS-WIDE cache keyed by
        credential (#285): the server creates a new client instance on every
        MCP call, an instance cache would cause one refresh per call — and Zoho
        then rate-limits `/oauth/v2/token` (all calls return 400 for ~5 min)."""
        return get_access_token(self.accounts_url, self.client_id,
                                self.client_secret, self.refresh_token,
                                key=self._cred_key, on_refresh=self._on_refresh)

    def _invalidate_token(self):
        """Forget the cached token to force a refresh on next request."""
        invalidate(self._cred_key)

    # --- HTTP ---

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{self.api_domain}/crm/{self.API_VERSION}/{endpoint}"
        token = self._get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}

        for attempt in range(3):
            resp = requests.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kwargs)

            # Token expired — refresh once and retry
            if resp.status_code == 401 and attempt == 0:
                self._invalidate_token()
                token = self._get_access_token()
                headers["Authorization"] = f"Zoho-oauthtoken {token}"
                continue

            # Rate limited
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                time.sleep(wait)
                continue

            raise_for_upstream(resp, service="zoho")

            return resp.json() if resp.content else {}

        raise Exception("Request failed after retries")

    # Default fields per module (avoids needing settings.fields scope)
    DEFAULT_FIELDS = {
        "Contacts": "First_Name,Last_Name,Email,Phone,Account_Name,Title",
        "Leads": "First_Name,Last_Name,Company,Email,Phone,Lead_Status",
        "Deals": "Deal_Name,Stage,Amount,Closing_Date,Account_Name,Contact_Name",
        "Accounts": "Account_Name,Website,Industry,Phone,Annual_Revenue",
        "Tasks": "Subject,Status,Due_Date,Priority,Owner",
        "Calls": "Subject,Call_Type,Call_Start_Time,Call_Duration,Owner",
        "Events": "Event_Title,Start_DateTime,End_DateTime,Location,Owner",
        "Campaigns": "Campaign_Name,Type,Status,Start_Date,End_Date",
        "Products": "Product_Name,Product_Code,Unit_Price,Qty_in_Stock",
        "Quotes": "Subject,Quote_Stage,Grand_Total,Valid_Till,Account_Name",
        "Invoices": "Subject,Status,Grand_Total,Due_Date,Account_Name",
    }

    # --- Modules ---

    def list_modules(self) -> list[dict]:
        """List available CRM modules."""
        data = self._request("GET", "settings/modules")
        return data.get("modules", [])

    # --- Records (generic CRUD) ---

    def list_records(
        self,
        module: str,
        page: int = 1,
        per_page: int = 200,
        fields: Optional[str] = None,
    ) -> dict:
        """List records from a module."""
        if not fields:
            fields = self.DEFAULT_FIELDS.get(module)
            if not fields:
                raise ValueError(
                    f"No default fields for module '{module}'. "
                    f"Pass --fields explicitly. Known modules: {', '.join(self.DEFAULT_FIELDS)}"
                )
        params: dict[str, Any] = {"page": page, "per_page": per_page, "fields": fields}
        return self._request("GET", module, params=params)

    def get_record(self, module: str, record_id: str) -> dict:
        """Get a single record."""
        data = self._request("GET", f"{module}/{record_id}")
        records = data.get("data", [])
        return records[0] if records else {}

    def search_records(
        self,
        module: str,
        criteria: str,
        page: int = 1,
        per_page: int = 200,
    ) -> dict:
        """Search records. criteria format: '(Field:operator:value)'."""
        params: dict[str, Any] = {
            "criteria": criteria,
            "page": page,
            "per_page": per_page,
        }
        return self._request("GET", f"{module}/search", params=params)

    def create_record(self, module: str, data: dict) -> dict:
        """Create a record."""
        body = {"data": [data]}
        return self._request("POST", module, json=body)

    def update_record(self, module: str, record_id: str, data: dict) -> dict:
        """Update a record."""
        body = {"data": [{"id": record_id, **data}]}
        return self._request("PUT", module, json=body)

    def delete_record(self, module: str, record_id: str) -> dict:
        """Delete a record."""
        return self._request("DELETE", f"{module}/{record_id}")

    # --- Notes ---

    def list_notes(self, module: str, record_id: str) -> list[dict]:
        """List notes for a record."""
        data = self._request("GET", f"{module}/{record_id}/Notes")
        return data.get("data", [])

    def create_note(self, module: str, record_id: str, title: str, content: str) -> dict:
        """Add a note to a record."""
        body = {"data": [{"Note_Title": title, "Note_Content": content}]}
        return self._request("POST", f"{module}/{record_id}/Notes", json=body)
