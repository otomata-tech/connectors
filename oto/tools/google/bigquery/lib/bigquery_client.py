"""Google BigQuery API client (REST v2) using OAuth2 user credentials.

Read surface only: projects, datasets, tables, table rows, and SQL queries. The
client sends whatever SQL it is given — refusing anything but a SELECT is the
consumer's job, using `dry_run` (the statement type is only known from a dry
run, never from the text).

Scope: `bigquery`. `bigquery.readonly` is NOT enough — `jobs.query`,
`jobs.getQueryResults` and `jobs.insert` (the dry run) don't accept it
(BigQuery v2 discovery document, revision 20260811).

Rows come back in BigQuery's `f`/`v` shape; `rows_to_records` turns them into
plain typed records using the result schema. Timestamps are requested as int64
microseconds (`useInt64Timestamp`) so they convert without float rounding.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from oto.tools.common.credentials import require

SCOPES = ['https://www.googleapis.com/auth/bigquery']

_INT_TYPES = {'INTEGER', 'INT64'}
_FLOAT_TYPES = {'FLOAT', 'FLOAT64'}
_BOOL_TYPES = {'BOOLEAN', 'BOOL'}
_RECORD_TYPES = {'RECORD', 'STRUCT'}


class BigQueryClientError(Exception):
    """BigQuery API error."""


def parse_http_error(e) -> dict:
    """`{status, reason, message, location}` from a `googleapiclient` HttpError.

    `reason` is BigQuery's own error reason (`invalidQuery`, `accessDenied`,
    `notFound`, `bytesBilledLimitExceeded`, `quotaExceeded`, …) — the thing to
    branch on, never the message text."""
    status = getattr(getattr(e, 'resp', None), 'status', None) or getattr(e, 'status_code', None)
    error: dict = {}
    try:
        payload = json.loads(e.content.decode()) if getattr(e, 'content', None) else {}
        error = payload.get('error') or {}
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    first = (error.get('errors') or [{}])[0]
    return {
        'status': int(status) if status else None,
        'reason': first.get('reason') or error.get('status') or '',
        'message': error.get('message') or first.get('message') or getattr(e, 'reason', '') or '',
        'location': first.get('location') or '',
    }


def split_table_ref(ref: str, default_project: Optional[str] = None) -> tuple[str, str, str]:
    """`project.dataset.table` (or `dataset.table` + default project) → 3-tuple.

    Backticks are tolerated (`` `p.d.t` ``), as an agent copies them from SQL."""
    parts = ref.replace('`', '').strip().split('.')
    if len(parts) == 2 and default_project:
        parts = [default_project, *parts]
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"table reference {ref!r} must be `project.dataset.table` "
            "(or `dataset.table` with a project)")
    return parts[0], parts[1], parts[2]


def query_parameters(params: Optional[dict]) -> list[dict]:
    """Named query parameters (`@name` in SQL) from a plain dict.

    Types are inferred: bool → BOOL, int → INT64, float → FLOAT64, anything else
    → STRING (CAST in SQL for DATE/TIMESTAMP). A list becomes an ARRAY of its
    first element's type; an empty list is refused (no type to infer)."""
    out = []
    for name, value in (params or {}).items():
        if isinstance(value, (list, tuple)):
            if not value:
                raise ValueError(f"parameter {name!r}: an empty list has no type — CAST in SQL instead")
            out.append({
                'name': name,
                'parameterType': {'type': 'ARRAY', 'arrayType': {'type': _param_type(value[0])}},
                'parameterValue': {'arrayValues': [{'value': _param_value(v)} for v in value]},
            })
        else:
            out.append({
                'name': name,
                'parameterType': {'type': _param_type(value)},
                'parameterValue': {'value': _param_value(value)},
            })
    return out


def _param_type(value: Any) -> str:
    if isinstance(value, bool):
        return 'BOOL'
    if isinstance(value, int):
        return 'INT64'
    if isinstance(value, float):
        return 'FLOAT64'
    return 'STRING'


def _param_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _cell(field: dict, v: Any) -> Any:
    if v is None:
        return None
    if field.get('mode') == 'REPEATED':
        item = {**field, 'mode': 'NULLABLE'}
        return [_cell(item, x.get('v') if isinstance(x, dict) else x) for x in v]
    t = (field.get('type') or '').upper()
    if t in _RECORD_TYPES:
        return _record(field.get('fields') or [], v)
    if t in _INT_TYPES:
        return int(v)
    if t in _FLOAT_TYPES:
        return float(v)
    if t in _BOOL_TYPES:
        return v in (True, 'true', 'TRUE', 'True')
    if t == 'TIMESTAMP':
        return _timestamp(v)
    # NUMERIC/BIGNUMERIC stay strings (exact decimals); DATE, DATETIME, TIME,
    # GEOGRAPHY, JSON, BYTES (base64) and STRING are already strings.
    return v


def _timestamp(v: Any) -> str:
    s = str(v)
    if '.' in s or 'e' in s.lower():  # float seconds (useInt64Timestamp not honoured)
        dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
    else:  # int64 microseconds
        us = int(s)
        dt = datetime.fromtimestamp(us // 1_000_000, tz=timezone.utc).replace(microsecond=us % 1_000_000)
    return dt.isoformat().replace('+00:00', 'Z')


def _record(fields: list, row: dict) -> dict:
    cells = (row or {}).get('f') or []
    return {f['name']: _cell(f, c.get('v')) for f, c in zip(fields, cells)}


def rows_to_records(schema: Optional[dict], rows: Optional[list]) -> list[dict]:
    """BigQuery `f`/`v` rows → `[{column: typed value}]`, following `schema`."""
    fields = (schema or {}).get('fields') or []
    return [_record(fields, r) for r in rows or []]


def flatten_schema(schema: Optional[dict], prefix: str = '') -> list[dict]:
    """Schema → flat `[{name, type, mode, description?}]`, nested fields as `a.b`."""
    out = []
    for f in (schema or {}).get('fields') or []:
        name = f"{prefix}{f['name']}"
        entry = {'name': name, 'type': f.get('type'), 'mode': f.get('mode') or 'NULLABLE'}
        if f.get('description'):
            entry['description'] = f['description']
        out.append(entry)
        if f.get('fields'):
            out.extend(flatten_schema({'fields': f['fields']}, prefix=f'{name}.'))
    return out


class BigQueryClient:
    """Google BigQuery API client.

    Args:
        credentials: OAuth2 user credentials, provided by the consumer (required).
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self.service = build('bigquery', 'v2', credentials=require(credentials, 'GOOGLE_CREDENTIALS'))

    # --- discovery ------------------------------------------------------------

    def list_projects(self, max_results: int = 100, page_token: Optional[str] = None) -> dict:
        """Projects the user can see in BigQuery → `{projects, next_page_token}`."""
        resp = self.service.projects().list(maxResults=max_results, pageToken=page_token).execute()
        return {
            'projects': [
                {'id': p.get('id') or (p.get('projectReference') or {}).get('projectId'),
                 'name': p.get('friendlyName')}
                for p in resp.get('projects', [])
            ],
            'next_page_token': resp.get('nextPageToken'),
        }

    def list_datasets(self, project: str, max_results: int = 200,
                      page_token: Optional[str] = None) -> dict:
        """Datasets of a project → `{datasets, next_page_token}`."""
        resp = self.service.datasets().list(
            projectId=project, maxResults=max_results, pageToken=page_token).execute()
        return {
            'datasets': [
                {'id': (d.get('datasetReference') or {}).get('datasetId'),
                 'location': d.get('location'),
                 'name': d.get('friendlyName')}
                for d in resp.get('datasets', [])
            ],
            'next_page_token': resp.get('nextPageToken'),
        }

    def list_tables(self, project: str, dataset: str, max_results: int = 200,
                    page_token: Optional[str] = None) -> dict:
        """Tables and views of a dataset → `{tables, total, next_page_token}`."""
        resp = self.service.tables().list(
            projectId=project, datasetId=dataset, maxResults=max_results,
            pageToken=page_token).execute()
        return {
            'tables': [
                {'id': (t.get('tableReference') or {}).get('tableId'),
                 'type': t.get('type'),
                 'partitioned_by': (t.get('timePartitioning') or {}).get('field')
                 or ((t.get('timePartitioning') or {}).get('type') and '_PARTITIONTIME')}
                for t in resp.get('tables', [])
            ],
            'total': resp.get('totalItems'),
            'next_page_token': resp.get('nextPageToken'),
        }

    def get_table(self, project: str, dataset: str, table: str) -> dict:
        """Raw table resource (schema, numRows, numBytes, partitioning, view query…)."""
        return self.service.tables().get(
            projectId=project, datasetId=dataset, tableId=table).execute()

    def list_rows(self, project: str, dataset: str, table: str, max_results: int = 10,
                  page_token: Optional[str] = None,
                  selected_fields: Optional[str] = None) -> dict:
        """Raw `tabledata.list` page — free (no query job, nothing billed).

        Fails on views (no stored data): query them instead."""
        return self.service.tabledata().list(
            projectId=project, datasetId=dataset, tableId=table, maxResults=max_results,
            pageToken=page_token, selectedFields=selected_fields,
            **{'formatOptions_useInt64Timestamp': True}).execute()

    # --- queries ----------------------------------------------------------------

    def dry_run(self, sql: str, project: str, *, location: Optional[str] = None,
                params: Optional[dict] = None) -> dict:
        """Validate a query and estimate its cost, without running it (free).

        Uses `jobs.insert` with `dryRun`: unlike `jobs.query`, its response carries
        `statistics.query.statementType` — the only reliable way to tell a SELECT
        from DML/DDL/scripts. → `{statement_type, bytes_processed, schema,
        referenced_tables}`."""
        job = {'configuration': {'dryRun': True, 'query': self._query_config(sql, params)}}
        if location:
            # A jobReference carries its own jobId (as the Google client libraries do):
            # the location rides on it, and the API documents jobId as required there.
            job['jobReference'] = {'projectId': project, 'location': location,
                                   'jobId': f'oto_dry_{uuid.uuid4().hex}'}
        resp = self.service.jobs().insert(projectId=project, body=job).execute()
        stats = (resp.get('statistics') or {}).get('query') or {}
        return {
            'statement_type': stats.get('statementType'),
            'bytes_processed': int(stats.get('totalBytesProcessed') or 0),
            'schema': stats.get('schema'),
            'referenced_tables': [
                f"{t.get('projectId')}.{t.get('datasetId')}.{t.get('tableId')}"
                for t in stats.get('referencedTables') or []
            ],
        }

    def query(self, sql: str, project: str, *, location: Optional[str] = None,
              params: Optional[dict] = None, max_results: int = 200,
              timeout_ms: int = 25_000, maximum_bytes_billed: Optional[int] = None,
              labels: Optional[dict] = None) -> dict:
        """Run a query (`jobs.query`), waiting up to `timeout_ms`. Raw response:
        `jobComplete` false ⇒ resume with `get_query_results(jobReference.jobId)`."""
        body: dict = {
            **self._query_config(sql, params),
            'maxResults': max_results,
            'timeoutMs': timeout_ms,
            'formatOptions': {'useInt64Timestamp': True},
        }
        if location:
            body['location'] = location
        if maximum_bytes_billed is not None:
            body['maximumBytesBilled'] = str(int(maximum_bytes_billed))
        if labels:
            body['labels'] = labels
        return self.service.jobs().query(projectId=project, body=body).execute()

    def get_query_results(self, project: str, job_id: str, *, location: Optional[str] = None,
                          page_token: Optional[str] = None, max_results: int = 200,
                          timeout_ms: int = 25_000) -> dict:
        """A page of a query's results (`jobs.getQueryResults`), waiting up to
        `timeout_ms` for an unfinished job. `location` is required for jobs outside
        the `US`/`EU` multi-regions."""
        return self.service.jobs().getQueryResults(
            projectId=project, jobId=job_id, location=location, pageToken=page_token,
            maxResults=max_results, timeoutMs=timeout_ms,
            **{'formatOptions_useInt64Timestamp': True}).execute()

    @staticmethod
    def _query_config(sql: str, params: Optional[dict]) -> dict:
        config: dict = {'query': sql, 'useLegacySql': False}
        qp = query_parameters(params)
        if qp:
            config['parameterMode'] = 'NAMED'
            config['queryParameters'] = qp
        return config
