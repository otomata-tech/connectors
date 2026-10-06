"""Airtable Web API client — https://airtable.com/developers/web/api/introduction

Airtable = **bases** (`appXXXX`), each made of **tables** (`tblXXXX`) whose
columns are **typed fields** (`fldXXXX`) and whose rows are **records** (`recXXXX`).
This client covers the whole "Base data" section of the Web API (records, comments,
attachments, CSV sync) plus the schema (tables/fields) and the list of bases, without
which a caller can neither pick a base nor write a field.

Auth: **Personal Access Token** (`Authorization: Bearer pat…`), created at
https://airtable.com/create/tokens. A PAT carries **scopes** AND a **list of explicitly
granted bases** — both are needed (see `whoami` / `list_bases`).

Two hosts: `https://api.airtable.com/v0` for everything, EXCEPT attachment upload, which
lives on `https://content.airtable.com/v0`.

**One method = one endpoint.** No loops here: no automatic pagination, no
splitting into batches, no courtesy delay. This is deliberate — all three require a time
budget and a partial accounting ("30 records written out of 50, then
429") that belong to the caller, not the client. The batch methods **refuse**
more than `MAX_RECORDS_PER_REQUEST` records instead of quietly splitting.

API limits to know BEFORE calling:

- **10 records maximum per request** on create / update / delete
  (`MAX_RECORDS_PER_REQUEST`). Not clearly documented on Airtable's side; it is the value
  applied by the official clients and pyairtable.
- **5 requests/second per base**, 50/s per token. Beyond that: **429**, and Airtable requires
  **30 seconds** of waiting before subsequent requests go through again. A caller under
  time constraints is therefore better off stopping and reporting than waiting.
- `list_records` returns **100 records per page** at most, and an opaque `offset` as long
  as there are more.
- `sync_csv`: 10,000 rows, 500 columns, **2 MB per request**, and its own limit of
  **20 requests / 5 minutes / base**.
- `upload_attachment`: **5 MB**, content in **base64**. Beyond that, go through a public
  URL in the attachment field (`{"url": …}` via `update_record`).
- `cell_format="string"` **requires** `time_zone` AND `user_locale` — otherwise 422.

⚠️ **`typecast=True` is not a simple type conversion: it is a schema mutation
triggered by a data write.** On a single/multi-select it **creates the missing
option**; on a *linked record* field it **creates a record in the linked
table**. And it only requires the `data.records:write` scope, never
`schema.bases:write`. This client's default is therefore `typecast` **not sent**
(= `false` on Airtable's side): an unexpected value fails outright instead of silently
widening the schema of a real base.

⚠️ **Field names vs identifiers.** Tables and fields are addressed by name or
by id. The **name changes** as soon as a human renames a column, and then silently breaks
the automation — `tbl…`/`fld…` are the stable path. `return_fields_by_field_id`
asks the API to return values keyed by field id rather than by name.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait


class AirtableClient:
    """Airtable Web API v0 client, PAT Bearer auth."""

    BASE_URL = "https://api.airtable.com/v0"
    # Attachment upload is the ONLY endpoint served by a different host.
    CONTENT_URL = "https://content.airtable.com/v0"

    #: HARD API cap on multiple create/update/delete.
    MAX_RECORDS_PER_REQUEST = 10

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Personal Access Token.
        """
        self.api_key = require(api_key, "AIRTABLE_API_KEY")

    # ------------------------------------------------------------------
    # Plumbing

    def _headers(self, content_type: str = "application/json") -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[Dict[str, Any]] = None,
        base_url: Optional[str] = None,
        data: Optional[str] = None,
        content_type: str = "application/json",
    ) -> Any:
        """One HTTP call. `data` = RAW body (CSV sync), mutually exclusive with `json`."""
        resp = requests.request(
            method,
            f"{base_url or self.BASE_URL}{path}",
            headers=self._headers(content_type),
            json=json,
            data=data.encode("utf-8") if isinstance(data, str) else data,
            params=params,
            timeout=_HTTP_TIMEOUT,
        )
        raise_for_upstream(resp, service="airtable")
        return resp.json() if resp.content else None

    @staticmethod
    def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
        """Drop keys set to `None` — a parameter that was not provided must not be sent."""
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _qbool(value: Optional[bool]) -> Optional[str]:
        """Boolean meant for the QUERY STRING — `requests` serializes `True` as `"True"`,
        which Airtable does not recognize (the reference clients coerce to `true`/`1`).
        Only concerns GET params; in a JSON body the boolean goes out as-is."""
        return None if value is None else ("true" if value else "false")

    @staticmethod
    def _seg(value: str) -> str:
        """A URL segment. A table or field NAME may contain spaces and `/`."""
        return quote(str(value), safe="")

    def _check_batch(self, records: List[Any], verb: str) -> None:
        if len(records) > self.MAX_RECORDS_PER_REQUEST:
            raise ValueError(
                f"airtable {verb}: {len(records)} records for a maximum of "
                f"{self.MAX_RECORDS_PER_REQUEST} per request. Split on the caller side "
                f"(and space out the requests: 5/s per base)."
            )

    @staticmethod
    def _sort_params(sort: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
        """`[{"field": "Name", "direction": "desc"}]` → `sort[0][field]`, `sort[0][direction]`."""
        out: Dict[str, str] = {}
        for i, rule in enumerate(sort or []):
            out[f"sort[{i}][field]"] = rule["field"]
            if rule.get("direction"):
                out[f"sort[{i}][direction]"] = rule["direction"]
        return out

    @staticmethod
    def _cell_format_params(
        cell_format: Optional[str], time_zone: Optional[str], user_locale: Optional[str]
    ) -> Dict[str, Any]:
        """`cellFormat="string"` requires `timeZone` AND `userLocale` — refused here, not as a 422."""
        if cell_format == "string" and not (time_zone and user_locale):
            raise ValueError(
                "airtable: cell_format='string' requires time_zone (e.g. 'Europe/Paris') "
                "AND user_locale (e.g. 'fr'). Otherwise use cell_format='json'."
            )
        return {"cellFormat": cell_format, "timeZone": time_zone, "userLocale": user_locale}

    # ==================================================================
    # Records — https://airtable.com/developers/web/api/list-records
    # ==================================================================

    def list_records(
        self,
        base_id: str,
        table: str,
        *,
        fields: Optional[List[str]] = None,
        filter_by_formula: Optional[str] = None,
        max_records: Optional[int] = None,
        page_size: Optional[int] = None,
        sort: Optional[List[Dict[str, str]]] = None,
        view: Optional[str] = None,
        cell_format: Optional[str] = None,
        time_zone: Optional[str] = None,
        user_locale: Optional[str] = None,
        return_fields_by_field_id: Optional[bool] = None,
        record_metadata: Optional[List[str]] = None,
        offset: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`GET /{baseId}/{table}` — ONE page of records (100 max).

        Returns `{"records": [{id, createdTime, fields}], "offset": …}`. The `offset` is
        only present if more pages remain; pass it back unchanged for the next one.

        `filter_by_formula` is an Airtable formula evaluated per row
        (e.g. `{Status}='Done'`). `record_metadata=["commentCount"]` adds the number of
        comments. `fields` restricts the columns returned — the first lever against
        a huge response.
        """
        params = self._clean({
            "filterByFormula": filter_by_formula,
            "maxRecords": max_records,
            "pageSize": page_size,
            "view": view,
            "offset": offset,
            "returnFieldsByFieldId": self._qbool(return_fields_by_field_id),
            "fields[]": fields,
            "recordMetadata[]": record_metadata,
            **self._cell_format_params(cell_format, time_zone, user_locale),
        })
        params.update(self._sort_params(sort))
        return self._request("GET", f"/{base_id}/{self._seg(table)}", params=params)

    def list_records_post(self, base_id: str, table: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /{baseId}/{table}/listRecords` — same thing, criteria in the BODY.

        Airtable's escape hatch when the query string gets too long (a big
        `filterByFormula`). `body` takes the same keys as in GET, in camelCase.
        """
        return self._request("POST", f"/{base_id}/{self._seg(table)}/listRecords", json=body)

    def get_record(
        self,
        base_id: str,
        table: str,
        record_id: str,
        *,
        cell_format: Optional[str] = None,
        time_zone: Optional[str] = None,
        user_locale: Optional[str] = None,
        return_fields_by_field_id: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`GET /{baseId}/{table}/{recordId}` — one record with all its fields."""
        params = self._clean({
            "returnFieldsByFieldId": self._qbool(return_fields_by_field_id),
            **self._cell_format_params(cell_format, time_zone, user_locale),
        })
        return self._request(
            "GET", f"/{base_id}/{self._seg(table)}/{record_id}", params=params
        )

    def create_records(
        self,
        base_id: str,
        table: str,
        records: List[Dict[str, Any]],
        *,
        typecast: Optional[bool] = None,
        return_fields_by_field_id: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`POST /{baseId}/{table}` — creates 1 to 10 records.

        `records` = `[{"fields": {"Name": "Ada"}}, …]`. Returns `{"records": [...]}` with the
        assigned `recXXXX`. Beyond 10: `ValueError` (see `_check_batch`).
        """
        self._check_batch(records, "create_records")
        body = self._clean({
            "records": records,
            "typecast": typecast,
            "returnFieldsByFieldId": return_fields_by_field_id,
        })
        return self._request("POST", f"/{base_id}/{self._seg(table)}", json=body)

    def update_record(
        self,
        base_id: str,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
        *,
        replace: bool = False,
        typecast: Optional[bool] = None,
        return_fields_by_field_id: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`PATCH` (or `PUT` if `replace`) `/{baseId}/{table}/{recordId}`.

        ⚠️ `replace=True` → **destructive PUT**: any field missing from the body is CLEARED.
        `PATCH` (default) only touches the fields passed.
        """
        body = self._clean({
            "fields": fields,
            "typecast": typecast,
            "returnFieldsByFieldId": return_fields_by_field_id,
        })
        return self._request(
            "PUT" if replace else "PATCH",
            f"/{base_id}/{self._seg(table)}/{record_id}",
            json=body,
        )

    def update_records(
        self,
        base_id: str,
        table: str,
        records: List[Dict[str, Any]],
        *,
        replace: bool = False,
        typecast: Optional[bool] = None,
        perform_upsert: Optional[Dict[str, Any]] = None,
        return_fields_by_field_id: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`PATCH` (or `PUT` if `replace`) `/{baseId}/{table}` — 1 to 10 records.

        Two modes:
        - **update**: each item carries its `id` (`{"id": "rec…", "fields": {…}}`).
        - **upsert**: `perform_upsert={"fieldsToMergeOn": ["Email"]}` (1 to 3 fields).
          Items WITHOUT an `id` are then matched against existing rows on those
          fields — found ⟹ updated, otherwise created. The response distinguishes
          `createdRecords` and `updatedRecords`.

        ⚠️ `replace=True` (PUT) clears the fields not passed, including in upsert.
        """
        self._check_batch(records, "update_records")
        body = self._clean({
            "records": records,
            "typecast": typecast,
            "performUpsert": perform_upsert,
            "returnFieldsByFieldId": return_fields_by_field_id,
        })
        return self._request(
            "PUT" if replace else "PATCH", f"/{base_id}/{self._seg(table)}", json=body
        )

    def delete_record(self, base_id: str, table: str, record_id: str) -> Dict[str, Any]:
        """`DELETE /{baseId}/{table}/{recordId}` — PERMANENT deletion of a row."""
        return self._request("DELETE", f"/{base_id}/{self._seg(table)}/{record_id}")

    def delete_records(self, base_id: str, table: str, record_ids: List[str]) -> Dict[str, Any]:
        """`DELETE /{baseId}/{table}?records[]=…` — 1 to 10 rows, PERMANENTLY."""
        self._check_batch(record_ids, "delete_records")
        return self._request(
            "DELETE", f"/{base_id}/{self._seg(table)}", params={"records[]": record_ids}
        )

    # ==================================================================
    # Comments — https://airtable.com/developers/web/api/list-comments
    # ==================================================================

    def list_comments(
        self,
        base_id: str,
        table: str,
        record_id: str,
        *,
        page_size: Optional[int] = None,
        offset: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`GET /{baseId}/{table}/{recordId}/comments` — newest to oldest.

        100 per page maximum. Each comment carries `author`, `text`,
        `parentCommentId` (reply in a thread), `reactions` and `mentioned`.
        """
        return self._request(
            "GET",
            f"/{base_id}/{self._seg(table)}/{record_id}/comments",
            params=self._clean({"pageSize": page_size, "offset": offset}),
        )

    def create_comment(
        self,
        base_id: str,
        table: str,
        record_id: str,
        text: str,
        *,
        parent_comment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /{baseId}/{table}/{recordId}/comments`.

        Mentioning someone is written `@[usrXXXXXXX]` in `text`. `parent_comment_id`
        replies in an existing thread.
        """
        body = self._clean({"text": text, "parentCommentId": parent_comment_id})
        return self._request(
            "POST", f"/{base_id}/{self._seg(table)}/{record_id}/comments", json=body
        )

    def update_comment(
        self, base_id: str, table: str, record_id: str, comment_id: str, text: str
    ) -> Dict[str, Any]:
        """`PATCH /{baseId}/{table}/{recordId}/comments/{commentId}` — only `text` changes.

        A PAT can only edit the comments of ITS OWN user.
        """
        return self._request(
            "PATCH",
            f"/{base_id}/{self._seg(table)}/{record_id}/comments/{comment_id}",
            json={"text": text},
        )

    def delete_comment(
        self, base_id: str, table: str, record_id: str, comment_id: str
    ) -> Dict[str, Any]:
        """`DELETE /{baseId}/{table}/{recordId}/comments/{commentId}`.

        Deleting the top comment of a thread deletes the whole thread.
        """
        return self._request(
            "DELETE", f"/{base_id}/{self._seg(table)}/{record_id}/comments/{comment_id}"
        )

    # ==================================================================
    # Attachments — different HOST (content.airtable.com)
    # ==================================================================

    def upload_attachment(
        self,
        base_id: str,
        record_id: str,
        field: str,
        *,
        filename: str,
        content_type: str,
        file_b64: str,
    ) -> Dict[str, Any]:
        """`POST content.airtable.com/v0/{baseId}/{recordId}/{field}/uploadAttachment`.

        `file_b64` = the file content encoded in **base64** (5 MB max). ADDS an
        attachment to the field, without overwriting the previous ones. Returns the updated record.

        Beyond 5 MB: host the file and set `[{"url": …}]` in the field via
        `update_record` — Airtable fetches it itself.
        """
        return self._request(
            "POST",
            f"/{base_id}/{record_id}/{self._seg(field)}/uploadAttachment",
            json={"contentType": content_type, "file": file_b64, "filename": filename},
            base_url=self.CONTENT_URL,
        )

    # ==================================================================
    # CSV sync — RAW text/csv body
    # ==================================================================

    def sync_csv(self, base_id: str, table: str, sync_id: str, csv_data: str) -> Any:
        """`POST /{baseId}/{table}/sync/{apiEndpointSyncId}` — raw `text/csv` body.

        Feeds a **"Sync API"** table: the table must have been created in Airtable
        via this sync mode, which produces the `apiEndpointSyncId` (settings
        of the synced table). This is NOT an import into an ordinary table.

        Each send REPLACES the synced content (it is a source, not an append).
        Limits: 10,000 rows, 500 columns, 2 MB, **20 requests / 5 min / base**.
        """
        return self._request(
            "POST",
            f"/{base_id}/{self._seg(table)}/sync/{sync_id}",
            data=csv_data,
            content_type="text/csv",
        )

    # ==================================================================
    # Base schema — tables and fields
    # ==================================================================

    def get_base_schema(
        self, base_id: str, *, include: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """`GET /meta/bases/{baseId}/tables` — ALL the tables of a base.

        Each table returns `id`, `name`, `description`, `primaryFieldId`, its `fields`
        (`id`, `name`, `type`, `options`) and its `views`. This is the only schema
        read: there is no "get one table" endpoint.

        `include=["visibleFieldIds"]` adds, for grid views, the visible fields.
        Scope `schema.bases:read`.
        """
        return self._request(
            "GET",
            f"/meta/bases/{base_id}/tables",
            params=self._clean({"include[]": include}),
        )

    def create_table(
        self,
        base_id: str,
        name: str,
        fields: List[Dict[str, Any]],
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /meta/bases/{baseId}/tables` — new table in an existing base.

        `fields` = `[{"name": …, "type": …, "options": {…}}]`. ⚠️ **The FIRST field
        becomes the primary field** and must be of a type allowed as such (text,
        number, date, formula… not an attachment or a checkbox).
        Scope `schema.bases:write`.
        """
        body = self._clean({"name": name, "fields": fields, "description": description})
        return self._request("POST", f"/meta/bases/{base_id}/tables", json=body)

    def update_table(
        self,
        base_id: str,
        table_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`PATCH /meta/bases/{baseId}/tables/{tableId}` — renames / redescribes a table.

        Only `name` and `description` are modifiable; the structure goes through the
        fields. Scope `schema.bases:write`.
        """
        body = self._clean({"name": name, "description": description})
        return self._request("PATCH", f"/meta/bases/{base_id}/tables/{table_id}", json=body)

    def create_field(
        self,
        base_id: str,
        table_id: str,
        name: str,
        type: str,
        *,
        description: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """`POST /meta/bases/{baseId}/tables/{tableId}/fields` — new column.

        `type` = an Airtable type (`singleLineText`, `number`, `singleSelect`,
        `multipleRecordLinks`, `checkbox`…). `options` is **required by most
        types** and its shape depends on the type (a `singleSelect` wants `{"choices": [{"name":
        …}]}`, a `number` wants `{"precision": 0}`, a `multipleRecordLinks` wants
        `{"linkedTableId": …}`). Scope `schema.bases:write`.
        """
        body = self._clean({
            "name": name, "type": type, "description": description, "options": options
        })
        return self._request(
            "POST", f"/meta/bases/{base_id}/tables/{table_id}/fields", json=body
        )

    def update_field(
        self,
        base_id: str,
        table_id: str,
        field_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`PATCH /meta/bases/{baseId}/tables/{tableId}/fields/{fieldId}`.

        Only `name` and `description` are modifiable: the API **changes neither the type nor
        the `options`** of an existing field. Verified live on 2026-08-25 — a PATCH
        carrying `options` returns `422 INVALID_REQUEST_UNKNOWN, "Changing a field's type or
        number precision is not currently supported."`, and there is **no**
        `DELETE …/fields/{id}` (404). A select option added by mistake (via
        `typecast`) can therefore ONLY be removed in the Airtable interface.
        Scope `schema.bases:write`.
        """
        body = self._clean({"name": name, "description": description})
        return self._request(
            "PATCH", f"/meta/bases/{base_id}/tables/{table_id}/fields/{field_id}", json=body
        )

    # ==================================================================
    # Bases and token identity
    # ==================================================================

    def list_bases(self, *, offset: Optional[str] = None) -> Dict[str, Any]:
        """`GET /meta/bases` — the bases GRANTED to the token (1000 per page).

        Returns `{"bases": [{id, name, permissionLevel}], "offset": …}`.
        ⚠️ A perfectly valid PAT that has been granted no base returns an **empty
        list here, with a 200** — not an error. This is Airtable's most
        frequent failure mode. Scope `schema.bases:read`.
        """
        return self._request("GET", "/meta/bases", params=self._clean({"offset": offset}))

    def create_base(
        self, name: str, workspace_id: str, tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """`POST /meta/bases` — new base in a workspace (`wspXXXX`).

        `tables` has the same shape as in `create_table` (at least one table, whose
        first field will be the primary field). There is **no** base deletion
        endpoint in the Web API. Scope `schema.bases:write`.
        """
        return self._request(
            "POST", "/meta/bases",
            json={"name": name, "workspaceId": workspace_id, "tables": tables},
        )

    def whoami(self) -> Dict[str, Any]:
        """`GET /meta/whoami` — the token's user (`id`, `email` if scope, `scopes`).

        Requires no scope: it is the pure authentication probe. It says NOTHING
        about accessible bases — cross-check with `list_bases`.
        """
        return self._request("GET", "/meta/whoami")
