"""BigQuery client: request shapes (offline, on the real discovery document) and
the pure conversions (f/v rows, parameters, table refs, errors)."""
import json

import pytest

pytest.importorskip("googleapiclient")
from google.oauth2.credentials import Credentials  # noqa: E402
from googleapiclient.http import HttpRequest  # noqa: E402

from oto.tools.google.bigquery.lib import bigquery_client as bq  # noqa: E402


@pytest.fixture
def sent(monkeypatch):
    """Capture every request instead of sending it."""
    calls = []

    def fake_execute(self, *a, **kw):
        calls.append(self)
        return {}

    monkeypatch.setattr(HttpRequest, "execute", fake_execute)
    return calls


@pytest.fixture
def client():
    return bq.BigQueryClient(credentials=Credentials(token="t"))


def test_scope_is_bigquery_not_readonly():
    # jobs.query / getQueryResults / insert reject `bigquery.readonly`.
    assert bq.SCOPES == ["https://www.googleapis.com/auth/bigquery"]


def test_query_body(client, sent):
    client.query("SELECT @n", "proj", location="EU", params={"n": 3},
                 max_results=50, timeout_ms=20_000, maximum_bytes_billed=10 * 1024**3,
                 labels={"source": "oto"})
    req = sent[0]
    assert req.method == "POST"
    assert req.uri.startswith("https://bigquery.googleapis.com/bigquery/v2/projects/proj/queries")
    body = json.loads(req.body)
    assert body["useLegacySql"] is False
    assert body["maximumBytesBilled"] == str(10 * 1024**3)
    assert body["location"] == "EU"
    assert body["timeoutMs"] == 20_000 and body["maxResults"] == 50
    assert body["formatOptions"] == {"useInt64Timestamp": True}
    assert body["parameterMode"] == "NAMED"
    assert body["queryParameters"][0]["parameterType"] == {"type": "INT64"}
    assert body["labels"] == {"source": "oto"}


def test_query_without_cap_sends_none(client, sent):
    client.query("SELECT 1", "proj")
    body = json.loads(sent[0].body)
    assert "maximumBytesBilled" not in body and "location" not in body


def test_dry_run_uses_jobs_insert(client, sent, monkeypatch):
    client.dry_run("SELECT 1", "proj", location="europe-west1")
    req = sent[0]
    assert req.uri.startswith("https://bigquery.googleapis.com/bigquery/v2/projects/proj/jobs")
    body = json.loads(req.body)
    assert body["configuration"]["dryRun"] is True
    assert body["configuration"]["query"]["useLegacySql"] is False
    assert body["jobReference"] == {"projectId": "proj", "location": "europe-west1"}


def test_dry_run_parses_statistics(client, monkeypatch):
    monkeypatch.setattr(HttpRequest, "execute", lambda self, *a, **k: {
        "statistics": {"query": {
            "statementType": "SELECT", "totalBytesProcessed": "2048",
            "schema": {"fields": [{"name": "a", "type": "INTEGER"}]},
            "referencedTables": [{"projectId": "p", "datasetId": "d", "tableId": "t"}]}}})
    out = client.dry_run("SELECT a FROM d.t", "p")
    assert out == {"statement_type": "SELECT", "bytes_processed": 2048,
                   "schema": {"fields": [{"name": "a", "type": "INTEGER"}]},
                   "referenced_tables": ["p.d.t"]}


def test_get_query_results_and_tabledata_ask_int64_timestamps(client, sent):
    client.get_query_results("proj", "job_1", location="asia-northeast1", page_token="pt")
    client.list_rows("proj", "ds", "tb", max_results=5)
    assert "/projects/proj/queries/job_1" in sent[0].uri
    assert "location=asia-northeast1" in sent[0].uri and "pageToken=pt" in sent[0].uri
    for req in sent:
        assert "formatOptions.useInt64Timestamp=true" in req.uri


def test_rows_to_records_types_and_nesting():
    schema = {"fields": [
        {"name": "id", "type": "INTEGER"},
        {"name": "score", "type": "FLOAT"},
        {"name": "ok", "type": "BOOLEAN"},
        {"name": "amount", "type": "NUMERIC"},
        {"name": "at", "type": "TIMESTAMP"},
        {"name": "tags", "type": "STRING", "mode": "REPEATED"},
        {"name": "owner", "type": "RECORD", "fields": [
            {"name": "name", "type": "STRING"},
            {"name": "age", "type": "INTEGER"}]},
        {"name": "missing", "type": "STRING"},
    ]}
    rows = [{"f": [
        {"v": "42"}, {"v": "1.5"}, {"v": "true"}, {"v": "12.30"},
        {"v": "1727870400123456"},
        {"v": [{"v": "a"}, {"v": "b"}]},
        {"v": {"f": [{"v": "Ana"}, {"v": "31"}]}},
        {"v": None},
    ]}]
    assert bq.rows_to_records(schema, rows) == [{
        "id": 42, "score": 1.5, "ok": True, "amount": "12.30",
        "at": "2024-10-02T12:00:00.123456Z",
        "tags": ["a", "b"], "owner": {"name": "Ana", "age": 31}, "missing": None,
    }]


def test_timestamp_float_seconds_fallback():
    assert bq._timestamp("1.7278704E9") == "2024-10-02T12:00:00Z"


def test_repeated_record():
    schema = {"fields": [{"name": "items", "type": "RECORD", "mode": "REPEATED",
                          "fields": [{"name": "sku", "type": "STRING"}]}]}
    rows = [{"f": [{"v": [{"v": {"f": [{"v": "x"}]}}, {"v": {"f": [{"v": "y"}]}}]}]}]
    assert bq.rows_to_records(schema, rows) == [{"items": [{"sku": "x"}, {"sku": "y"}]}]


def test_flatten_schema_paths():
    schema = {"fields": [{"name": "o", "type": "RECORD", "mode": "REPEATED",
                          "fields": [{"name": "k", "type": "STRING", "description": "key"}]}]}
    assert bq.flatten_schema(schema) == [
        {"name": "o", "type": "RECORD", "mode": "REPEATED"},
        {"name": "o.k", "type": "STRING", "mode": "NULLABLE", "description": "key"},
    ]


def test_query_parameters_inference():
    out = bq.query_parameters({"b": True, "i": 3, "f": 0.5, "s": "x", "l": ["a", "b"]})
    types = {p["name"]: p["parameterType"] for p in out}
    assert types == {"b": {"type": "BOOL"}, "i": {"type": "INT64"}, "f": {"type": "FLOAT64"},
                     "s": {"type": "STRING"},
                     "l": {"type": "ARRAY", "arrayType": {"type": "STRING"}}}
    assert out[0]["parameterValue"] == {"value": "true"}
    with pytest.raises(ValueError):
        bq.query_parameters({"e": []})


@pytest.mark.parametrize("ref,default,want", [
    ("p.d.t", None, ("p", "d", "t")),
    ("`p.d.t`", None, ("p", "d", "t")),
    ("d.t", "p", ("p", "d", "t")),
])
def test_split_table_ref(ref, default, want):
    assert bq.split_table_ref(ref, default) == want


@pytest.mark.parametrize("ref", ["t", "d.t", "a.b.c.d", "p..t"])
def test_split_table_ref_refuses(ref):
    with pytest.raises(ValueError):
        bq.split_table_ref(ref)


def test_parse_http_error():
    class E:
        resp = type("R", (), {"status": 400})()
        content = json.dumps({"error": {"code": 400, "message": "Unrecognized name: foo at [1:8]",
                                        "errors": [{"reason": "invalidQuery", "location": "q",
                                                    "message": "Unrecognized name: foo"}]}}).encode()
    assert bq.parse_http_error(E()) == {"status": 400, "reason": "invalidQuery",
                                        "message": "Unrecognized name: foo at [1:8]",
                                        "location": "q"}


def test_parse_http_error_non_json():
    class E:
        resp = type("R", (), {"status": 502})()
        content = b"<html>bad gateway</html>"
        reason = "Bad Gateway"
    assert bq.parse_http_error(E())["status"] == 502
    assert bq.parse_http_error(E())["message"] == "Bad Gateway"
