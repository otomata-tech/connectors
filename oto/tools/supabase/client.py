"""
Supabase Management API client.

Auth: SUPABASE_ACCESS_TOKEN (Personal Access Token `sbp_...`, created at
https://supabase.com/dashboard/account/tokens), supplied by the consumer.
API docs: https://api.supabase.com

Requires: requests
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require

BASE = "https://api.supabase.com"
# Explicit UA: the analytics endpoint returns a Cloudflare 403 (1010) on the
# default python-urllib User-Agent; a non-empty UA gets through.
_UA = "oto-supabase-client/1.0"


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {require(token, 'SUPABASE_ACCESS_TOKEN')}",
        "User-Agent": _UA,
        "Accept": "application/json",
    }


def _request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Any:
    resp = requests.request(
        method, f"{BASE}{path}", headers=_headers(token),
        params=params, json=json_body, timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else None


def list_projects(token: Optional[str] = None) -> List[Dict[str, Any]]:
    """List the projects accessible with this PAT."""
    return _request("GET", "/v1/projects", token=token)


def get_auth_config(project_ref: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Auth config of a project (site_url, uri_allow_list, providers, etc.)."""
    return _request("GET", f"/v1/projects/{project_ref}/config/auth", token=token)


def query_logs(
    project_ref: str,
    sql: Optional[str] = None,
    source: str = "auth_logs",
    limit: int = 50,
    minutes: int = 120,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query a project's logs (Logflare via the Management API).

    Args:
        project_ref: project ref (e.g. doebdriroupduqpggcsj).
        sql: Logflare SQL query. If None, latest lines of `source`.
        source: log table (auth_logs, edge_logs, function_edge_logs,
                function_logs, postgres_logs, postgrest_logs, storage_logs...).
        limit: number of lines (if sql is None).
        minutes: time window (the API requires an iso_timestamp_* range).

    Returns:
        List of rows (dict). Each row typically has `timestamp` + `event_message`.
    """
    if sql is None:
        sql = (
            f"select timestamp, event_message from {source} "
            f"order by timestamp desc limit {limit}"
        )
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    params = {
        "sql": sql,
        # RFC3339 format with Z suffix (the API rejects the +00:00 offset).
        "iso_timestamp_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iso_timestamp_end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    data = _request(
        "GET", f"/v1/projects/{project_ref}/analytics/endpoints/logs.all",
        params=params, token=token,
    )
    if isinstance(data, dict):
        return data.get("result", [])
    return data or []
