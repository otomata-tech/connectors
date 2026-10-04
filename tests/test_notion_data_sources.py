"""Notion API 2025-09-03: databases hold data sources.

Covers what failed on 03/10/2026 (org 396): a `search` filtered on "database"
(400: filter.value must be "page" or "data_source"); `create_page` under a
linked view of a database (400: "is a database, not a database"), with a
title column named "Idea" while a "Name" title was forced too;
`get_database` returning no columns. Stub of `requests.request`: no real
call to Notion.
"""
from __future__ import annotations

import pytest

from notion_fake import DB, DS, NEW, PAGE, _Resp, notion  # noqa: F401


def _schema(title="Name"):
    return {"object": "data_source", "id": DS,
            "properties": {title: {"type": "title"}, "Status": {"type": "select"}}}


def _posted_page(calls):
    return next(c["json"] for c in calls if c["endpoint"] == "pages")


def test_search_database_filter_becomes_data_source(notion):
    client, routes, calls = notion
    routes[("POST", "search")] = _Resp(body={"results": []})
    client.search("", filter_type="database")
    assert calls[0]["json"]["filter"] == {"value": "data_source", "property": "object"}


def test_search_edited_on_maps_the_filter_too(notion):
    client, routes, calls = notion
    routes[("POST", "search")] = _Resp(body={"results": [], "has_more": False})
    client.search_edited_on("2026-10-03", filter_type="database")
    assert calls[0]["json"]["filter"]["value"] == "data_source"


def test_create_row_with_a_data_source_id(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    client.create_page(DS, "database", "Hello")
    body = _posted_page(calls)
    assert body["parent"] == {"type": "data_source_id", "data_source_id": DS}
    assert body["properties"]["Name"] == {"title": [{"text": {"content": "Hello"}}]}


def test_create_row_with_a_database_id_uses_its_data_source(notion):
    client, routes, calls = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": [{"id": DS}]})
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    client.create_page(DB, "database", "Hello")
    assert _posted_page(calls)["parent"]["data_source_id"] == DS


def test_title_goes_in_the_real_title_column(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema(title="Tâche"))
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    client.create_page(DS, "database", "Hello", properties={"Status": {"select": {"name": "Todo"}}})
    props = _posted_page(calls)["properties"]
    assert "Name" not in props
    assert props["Tâche"]["title"][0]["text"]["content"] == "Hello"
    assert props["Status"] == {"select": {"name": "Todo"}}


def test_title_already_in_properties_is_kept(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    mine = {"Name": {"title": [{"text": {"content": "Mine"}}]}}
    client.create_page(DS, "database", "ignored", properties=mine)
    assert _posted_page(calls)["properties"] == mine


def test_page_parent_unchanged(notion):
    client, routes, calls = notion
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    client.create_page(PAGE, "page", "Sub")
    body = _posted_page(calls)
    assert body["parent"] == {"page_id": PAGE}
    assert body["properties"] == {"title": {"title": [{"text": {"content": "Sub"}}]}}


def test_more_than_100_blocks_are_appended_in_batches(notion):
    client, routes, calls = notion
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    routes[("PATCH", f"blocks/{NEW}/children")] = lambda kw: _Resp(
        body={"results": kw["json"]["children"]})
    blocks = [{"type": "paragraph", "n": i} for i in range(250)]
    client.create_page(PAGE, "page", "Long", content=blocks)
    assert len(_posted_page(calls)["children"]) == 100
    patches = [c["json"]["children"] for c in calls if c["method"] == "PATCH"]
    assert [len(p) for p in patches] == [100, 50]
    assert patches[1][-1]["n"] == 249


def test_get_database_accepts_a_data_source_id(notion):
    client, routes, _ = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    assert client.get_database(DS)["object"] == "data_source"


def test_get_database_on_a_page_says_it_is_a_page(notion):
    client, routes, _ = notion
    routes[("GET", f"databases/{PAGE}")] = _Resp(400, {
        "message": f"Provided database_id {PAGE} is a page, not a database.",
        "code": "validation_error"})
    with pytest.raises(Exception, match="is a PAGE"):
        client.get_database(PAGE)


def test_query_database_with_a_data_source_id(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", f"data_sources/{DS}/query")] = _Resp(body={"results": []})
    client.query_database(DS)
    assert calls[-1]["endpoint"] == f"data_sources/{DS}/query"


def test_title_in_a_custom_column_is_not_doubled_with_name(notion):
    """The 03/10 payload: the caller sets "Idea" itself, no "Name" is added."""
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema(title="Idea"))
    routes[("POST", "pages")] = _Resp(body={"id": NEW})
    client.create_page(DS, "database", "T", properties={
        "Idea": {"title": [{"text": {"content": "T"}}]},
        "Topic": {"select": {"name": "Organization"}}})
    assert set(_posted_page(calls)["properties"]) == {"Idea", "Topic"}


def test_linked_view_says_so(notion):
    client, routes, calls = notion
    linked = "44444444444444444444444444444444"
    routes[("GET", f"blocks/{linked}")] = _Resp(body={"type": "child_database"})
    with pytest.raises(Exception, match="LINKED VIEW"):
        client.create_page(linked, "database", "T")
    assert not [c for c in calls if c["endpoint"] == "pages"]


def test_get_database_carries_the_columns(notion):
    client, routes, _ = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": [{"id": DS}]})
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema(title="Idea"))
    db = client.get_database(DB)
    assert db["id"] == DB
    assert db["properties"]["Idea"] == {"type": "title"}


def test_append_after_a_block_keeps_batches_in_order(notion):
    client, routes, calls = notion
    routes[("PATCH", f"blocks/{PAGE}/children")] = lambda kw: _Resp(body={"results": [
        {"id": f"b{b['n']}"} for b in kw["json"]["children"]]})
    blocks = [{"type": "paragraph", "n": i} for i in range(150)]
    client.append_blocks(PAGE, blocks, position="top")
    positions = [c["json"]["position"] for c in calls if c["method"] == "PATCH"]
    assert positions == [
        {"type": "after_block", "after_block": {"id": "top"}},
        {"type": "after_block", "after_block": {"id": "b99"}}]


def test_append_at_start_then_keeps_order(notion):
    client, routes, calls = notion
    routes[("PATCH", f"blocks/{PAGE}/children")] = lambda kw: _Resp(body={"results": [
        {"id": f"b{b['n']}"} for b in kw["json"]["children"]]})
    client.append_blocks(PAGE, [{"type": "paragraph", "n": i} for i in range(101)],
                         position="start")
    positions = [c["json"]["position"] for c in calls if c["method"] == "PATCH"]
    assert positions[0] == {"type": "start"}
    assert positions[1] == {"type": "after_block", "after_block": {"id": "b99"}}


def test_append_by_default_sends_no_position(notion):
    client, routes, calls = notion
    routes[("PATCH", f"blocks/{PAGE}/children")] = _Resp(body={"results": []})
    client.append_blocks(PAGE, [{"type": "paragraph"}])
    assert "position" not in calls[0]["json"]
    assert "after" not in calls[0]["json"]
