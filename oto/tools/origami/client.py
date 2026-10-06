"""Origami API client — lead tables, email + LinkedIn campaigns (origami.chat).

API v2 (`https://origami.chat/api/v2`, docs https://docs.origami.chat, spec
https://docs.origami.chat/openapi-v2.yaml), auth **Bearer** `og_live_…`. One method =
one endpoint; bodies and responses pass through as-is (JSON), the client invents
no semantics. Endpoints verified live on 16–17/08/2026.

v2 conventions to know (they condition the caller):
- **Lists**: envelope `{object: "list", items: [...], nextCursor: str|null, url}` —
  page of 50 by default; the caller FOLLOWS `nextCursor` (passed back as `cursor`).
- **Errors**: `{error, code, details?, handoff?}`, `code` in uppercase SNAKE_CASE
  (`UNKNOWN_FIELDS`, `TABLE_NOT_FOUND`, `MISSING_SCOPE`…) — surfaced as-is
  in `UpstreamHTTPError.body`.
- **Writes**: `POST` everywhere (row upsert, campaign creation/launch) —
  this client is NOT read-only. `dryRun`/`confirm` are query params
  on the API side; they are the only safeguards the server offers.
- **Deletions**: two steps (`DELETE` without `confirm` = impact preview, then
  `?confirm=true`). ⚠️ Verified: the 2nd step can answer 200 WITHOUT deleting — a
  caller that wants certainty re-GETs the resource and requires a 404.
- **Slugs**: row keys and `matchColumns` are SLUGS of input columns
  (`GET /tables/{id}/columns` → `items[].slug`, dashes), never displayed names;
  an unknown slug → 400 `UNKNOWN_FIELDS`; a non-input column → `NON_INPUT_COLUMNS`.
- **Upload**: `POST /workspaces/{id}/documents` as JSON (bytes in base64), NEVER
  multipart; a CSV with `mode: "table"` CREATES a table.
- **Campaigns**: `POST /tables/{id}/campaigns` is AGENTIC (the Origami agent
  drafts the campaign from `instructions`) → 202 `{agent: {id}, run: {id}}`;
  follow `GET /agents/{aid}/runs/{rid}` (there is NO `GET /runs/{id}`).
  There is no global `GET /campaigns` either: list by table
  (`/tables/{id}/campaigns`); `GET /sequences?workspaceId=` is the view that sees
  all the sequences (one per enrolled person) of a workspace.
- **Projects**: the key is parent-wide; the `x-origami-project: <projectId>` header
  scopes the request to a project (child org). Optional (`project_id`), omitted =
  the parent org.

Requires: requests
"""
from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# (connect, read) — campaign creation and upload can be slow.
_HTTP_TIMEOUT = (10, 120)


class OrigamiClient:
    """Origami v2 client (https://origami.chat/api/v2), Bearer auth `og_live_…`."""

    BASE_URL = "https://origami.chat/api/v2"

    def __init__(self, api_key: Optional[str] = None,
                 project_id: Optional[str] = None):
        """
        Args:
            api_key: Origami key.
            project_id: project id (child org) → `x-origami-project` header.
                Omitted = the request acts on the key's parent org.
        """
        self.api_key = require(api_key, "ORIGAMI_API_KEY")
        self.project_id = project_id
        self.session = requests.Session()
        # Key in the HEADER only (never in the query string: it would end up in the URL,
        # hence in every exception message, the logs and Sentry).
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if project_id:
            self.session.headers["x-origami-project"] = project_id

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None) -> Any:
        # Query params that are None are dropped; booleans go out as `true`/`false`
        # (requests would write `True`, which the server does not read as a boolean).
        clean: Dict[str, Any] = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            clean[k] = ("true" if v else "false") if isinstance(v, bool) else v
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", params=clean or None, json=json,
            timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="origami")
        return resp.json() if resp.content else {}

    # --- workspaces ---------------------------------------------------------

    def list_workspaces(self, cursor: Optional[str] = None,
                        limit: Optional[int] = None,
                        search: Optional[str] = None) -> Dict[str, Any]:
        """GET /workspaces — list envelope (`items[]` of `{id, name, url,
        createdAt…}`, `nextCursor`). `search` = substring of the name."""
        return self._request("GET", "/workspaces",
                             params={"cursor": cursor, "limit": limit, "search": search})

    def create_workspace(self, name: str) -> Dict[str, Any]:
        """POST /workspaces — creates a workspace ("upload first" flow) → 201
        `{id, name, …}`. 403 `WORKSPACE_LIMIT_REACHED` if the plan is full."""
        if not name or not str(name).strip():
            raise ValueError("create_workspace: `name` required.")
        return self._request("POST", "/workspaces", json={"name": name})

    def upload_documents(self, workspace_id: str,
                         files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """POST /workspaces/{id}/documents — THE ingestion verb (JSON, never
        multipart). `files` = `[{filename, content (base64), mode?, tableId?}]`;
        `mode` ∈ table (CSV → NEW table, default for .csv) | append (CSV →
        existing table, `tableId` required) | document. All-or-nothing preflight;
        201 as soon as one file has landed (`results[]`, `kind: "error"` entries
        possible), 422 `UPLOAD_FAILED` if all failed."""
        if not files:
            raise ValueError("upload_documents: `files` empty.")
        for f in files:
            if not isinstance(f, dict) or not f.get("filename") or not f.get("content"):
                raise ValueError(
                    "upload_documents: each file = {filename, content (base64)[, mode, tableId]}.")
        return self._request("POST", f"/workspaces/{workspace_id}/documents",
                             json={"files": list(files)})

    # --- tables -------------------------------------------------------------

    def list_tables(self, workspace_id: Optional[str] = None,
                    cursor: Optional[str] = None,
                    limit: Optional[int] = None) -> Dict[str, Any]:
        """GET /tables[?workspaceId=] — list envelope of tables (`{id, workspaceId,
        name, leadCount, columns[], credits, url…}`)."""
        return self._request("GET", "/tables",
                             params={"workspaceId": workspace_id, "cursor": cursor,
                                     "limit": limit})

    def get_table(self, table_id: str, include: Optional[str] = None) -> Dict[str, Any]:
        """GET /tables/{id} — name, leadCount, columns, credits consumed
        (`credits.lifetimeUsed`); `include="stats"` adds the table's economics
        (creditsPerLead, qualification, funnel)."""
        return self._request("GET", f"/tables/{table_id}", params={"include": include})

    def list_columns(self, table_id: str) -> Dict[str, Any]:
        """GET /tables/{id}/columns — `items[]` of `{id, name, slug, kind, autoTrigger}`.
        The `slug`s are the keys to use in `upsert_rows` (columns with `kind ==
        "input"` only)."""
        return self._request("GET", f"/tables/{table_id}/columns")

    # --- rows ---------------------------------------------------------------

    def list_rows(self, table_id: str, cursor: Optional[str] = None,
                  cells: Optional[str] = "flat", limit: Optional[int] = None,
                  filters: Optional[List[Dict[str, Any]]] = None,
                  sort: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET /tables/{id}/rows?cells=flat[&cursor=] — ONE page (50 by default,
        `limit` ≤ 200) + `total`; the caller follows `nextCursor`. `cells="flat"`
        returns `{slug: value}` per row (`None` = polymorphic typed cells).
        `filters` = `[{column, operator, value}]` and `sort` = `{column, direction}`
        (slugs), serialized as JSON in the query."""
        params: Dict[str, Any] = {"cursor": cursor, "cells": cells, "limit": limit}
        if filters:
            params["filters"] = _json.dumps(filters)
        if sort:
            params["sort"] = _json.dumps(sort)
        return self._request("GET", f"/tables/{table_id}/rows", params=params)

    def upsert_rows(self, table_id: str, rows: List[Dict[str, Any]],
                    match_columns: List[str], enrich: bool = False,
                    reenrich_updated: Optional[bool] = None,
                    batch_id: Optional[str] = None) -> Dict[str, Any]:
        """POST /tables/{id}/rows/upsert — THE only row write (1–100 rows
        per call). A row for which ALL the `match_columns` values equal an existing
        row updates it, otherwise it is inserted. Keys = slugs of INPUT columns
        (dashes) — unknown slug → 400 `UNKNOWN_FIELDS`, non-input column →
        `NON_INPUT_COLUMNS`, empty match value → `MISSING_MATCH_VALUE`,
        duplicate within the request → `DUPLICATE_MATCH_KEY`, several existing rows
        for one key → 409 `AMBIGUOUS_MATCH`.

        `enrich`: the API DEFAULT is true (enrich inserted rows, spends
        credits); here False by default — enrichment must be requested explicitly.
        `reenrich_updated`: also re-enrich updated rows (spends again).
        Returns an `enrichment_run` `{id, batchId, counts: {inserted, updated,
        skipped}}` to follow via GET /enrichment-runs/{id}."""
        if not rows:
            raise ValueError("upsert_rows: `rows` empty.")
        if len(rows) > 100:
            raise ValueError(f"upsert_rows: {len(rows)} rows > 100 per call — split.")
        if not match_columns:
            raise ValueError("upsert_rows: `match_columns` required (slugs of input columns).")
        body: Dict[str, Any] = {
            "rows": list(rows),
            "matchColumns": list(match_columns),
            "enrich": bool(enrich),
        }
        if reenrich_updated is not None:
            body["reenrichUpdated"] = bool(reenrich_updated)
        if batch_id:
            body["batchId"] = batch_id
        return self._request("POST", f"/tables/{table_id}/rows/upsert", json=body)

    # --- campaigns ----------------------------------------------------------

    def list_campaigns(self, table_id: str) -> Dict[str, Any]:
        """GET /tables/{id}/campaigns — the campaigns that send from this table
        (`items[]` of `{id, slug, name, status, peopleCount}`, `nextCursor: null`).
        There is NO global `GET /campaigns`."""
        return self._request("GET", f"/tables/{table_id}/campaigns")

    def get_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """GET /campaigns/{id} — `{id, slug, name, status (draft|active|paused),
        workspaceId, tableId, channels: {email, linkedin}, settings:
        {blockActiveDuplicates, blockPriorContacts, autoTopUpEnabled}, brief…}`."""
        return self._request("GET", f"/campaigns/{campaign_id}")

    def campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        """GET /campaigns/{id}/stats — `{found, contacted, connectSent,
        connectAccepted, connectionRate, replied, replyRate, hasEmail, hasLinkedin}`."""
        return self._request("GET", f"/campaigns/{campaign_id}/stats")

    def campaign_people(self, campaign_id: str, cursor: Optional[str] = None,
                        limit: Optional[int] = None, status: Optional[str] = None,
                        search: Optional[str] = None) -> Dict[str, Any]:
        """GET /campaigns/{id}/people — the enrolled people (one sequence per
        person): `{sequenceId, rowId, recipient, sendStatus, stopReason,
        fitScore, fitExplanation, profile, addedAt}` + `total`. Paginated by
        `cursor`; `status` = CSV of send statuses; `search` = substring."""
        return self._request("GET", f"/campaigns/{campaign_id}/people",
                             params={"cursor": cursor, "limit": limit,
                                     "status": status, "search": search})

    def create_campaign(self, table_id: str, instructions: str,
                        settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST /tables/{id}/campaigns — AGENTIC creation: the Origami agent drafts
        the campaign (channels, sequences) from `instructions` (1–10,000 chars).
        Body `{instructions, settings?}`; `settings` = `{blockPriorContacts,
        blockActiveDuplicates}` (verified live 16–17/08/2026). Answers 202
        `{agent: {id}, run: {id}, table}` — follow `get_run(agent_id, run_id)`
        until `status != "running"`; the campaign then appears in
        `list_campaigns(table_id)`. Sends nothing: launching is another call.
        402 `INSUFFICIENT_CREDITS`, 409 `AGENT_BUSY`, 429 `CONCURRENT_LIMIT_EXCEEDED`."""
        if not instructions or not str(instructions).strip():
            raise ValueError("create_campaign: `instructions` required.")
        body: Dict[str, Any] = {"instructions": instructions}
        if settings:
            body["settings"] = dict(settings)
        return self._request("POST", f"/tables/{table_id}/campaigns", json=body)

    def get_run(self, agent_id: str, run_id: str,
                include: Optional[str] = None) -> Dict[str, Any]:
        """GET /agents/{aid}/runs/{rid} — the run (`status`: running | terminal,
        `steps`, `response.tables[]`…). This is THE tracking path after a campaign
        creation — there is NO `GET /runs/{id}`. `include="stats,transcript"`
        optional."""
        return self._request("GET", f"/agents/{agent_id}/runs/{run_id}",
                             params={"include": include})

    def launch_campaign(self, campaign_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """POST /campaigns/{id}/launch[?dryRun=true] — sets the campaign `active` and
        runs the launch pipeline (sender-account gate, duplicate
        cancellation, scheduling): this is the call that SENDS. Idempotent on an
        already active campaign. `dry_run=True` → `{dryRun: true, campaignId,
        wouldLaunch}` with no write. The real result carries `launched` and
        `launch: {scheduled, firstScheduledAt, missingRecipientCount, …,
        blocked?: {reason, message, missingChannels[]}}` — `blocked` = no sender
        account for those channels, NOTHING went out."""
        return self._request("POST", f"/campaigns/{campaign_id}/launch",
                             params={"dryRun": True} if dry_run else None)

    def pause_campaign(self, campaign_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """POST /campaigns/{id}/pause[?dryRun=true] — pauses (idempotent).
        Real result: `pause: {stoppedSequences, haltedSteps, inFlightSending,
        alreadyPaused}`."""
        return self._request("POST", f"/campaigns/{campaign_id}/pause",
                             params={"dryRun": True} if dry_run else None)

    def resume_campaign(self, campaign_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """POST /campaigns/{id}/resume[?dryRun=true] — resumes where the sequences
        had stopped (idempotent). Real result: `resume: {resumedSequences,
        noAccountSequences, missingChannels[]}`."""
        return self._request("POST", f"/campaigns/{campaign_id}/resume",
                             params={"dryRun": True} if dry_run else None)

    def delete_campaign(self, campaign_id: str, confirm: bool = False,
                        dry_run: bool = False) -> Dict[str, Any]:
        """DELETE /campaigns/{id}[?confirm=true][&dryRun=true] — deletion in TWO
        steps. Without `confirm` (or with `dry_run`): impact preview `{id, name,
        confirmationRequired: true, status}`, nothing is removed. With
        `confirm=True`: soft-delete + cancellation of orphaned sequences →
        `{id, name, deleted: true}`.

        ⚠️ Verified live: the 2nd step can answer 200 without the campaign
        disappearing. A caller that needs certainty re-GETs the campaign
        (`get_campaign`) and requires a 404 (`UpstreamHTTPError.status_code == 404`)."""
        params: Dict[str, Any] = {}
        if confirm:
            params["confirm"] = True
        if dry_run:
            params["dryRun"] = True
        return self._request("DELETE", f"/campaigns/{campaign_id}", params=params or None)

    # --- sequences ----------------------------------------------------------

    def list_sequences(self, workspace_id: str, cursor: Optional[str] = None,
                       limit: Optional[int] = None, status: Optional[str] = None,
                       channel: Optional[str] = None,
                       recipient: Optional[str] = None) -> Dict[str, Any]:
        """GET /sequences?workspaceId= — ALL the sequences (one per enrolled
        person, each `item` carries its `campaignId`) of a workspace: the only view
        that sees every campaign, whatever its table. `workspaceId` is
        required by the API (otherwise 400 `MISSING_SCOPE`). `status` / `channel`
        / `recipient` filters, `cursor` pagination."""
        if not workspace_id:
            raise ValueError("list_sequences: `workspace_id` required (400 MISSING_SCOPE otherwise).")
        return self._request("GET", "/sequences",
                             params={"workspaceId": workspace_id, "cursor": cursor,
                                     "limit": limit, "status": status,
                                     "channel": channel, "recipient": recipient})

    def get_sequence(self, sequence_id: str) -> Dict[str, Any]:
        """GET /sequences/{id} — a sequence with its steps inline (provider
        internals are redacted)."""
        return self._request("GET", f"/sequences/{sequence_id}")
