"""Notion surface beyond pages: comments, database schema, views, moves,
Markdown, block edits, users, pagination. Request shapes follow Notion's API
reference (2025-09-03 + additive endpoints). Stub of `requests.request`: no
real call to Notion.
"""
from __future__ import annotations

import pytest

from notion_fake import DB, DS, PAGE, _Resp, notion  # noqa: F401


def _schema():
    return {"object": "data_source", "id": DS,
            "parent": {"type": "database_id", "database_id": DB},
            "properties": {"Name": {"type": "title"}}}


def _last(calls, method):
    return [c for c in calls if c["method"] == method][-1]


# --- comments ---------------------------------------------------------------

def test_add_comment_on_a_page(notion):
    client, routes, calls = notion
    routes[("POST", "comments")] = _Resp(body={"id": "c1"})
    client.add_comment("**hi**", page_id=PAGE)
    assert calls[0]["json"] == {"markdown": "**hi**", "parent": {"page_id": PAGE}}


def test_reply_in_a_discussion(notion):
    client, routes, calls = notion
    routes[("POST", "comments")] = _Resp(body={"id": "c1"})
    client.add_comment("ok", discussion_id="d1")
    assert calls[0]["json"] == {"markdown": "ok", "discussion_id": "d1"}


def test_comment_needs_exactly_one_target(notion):
    client, _, _ = notion
    with pytest.raises(ValueError):
        client.add_comment("x")
    with pytest.raises(ValueError):
        client.add_comment("x", page_id=PAGE, block_id="b")


def test_list_comments_passes_block_and_cursor(notion):
    client, routes, calls = notion
    routes[("GET", "comments")] = _Resp(body={"results": []})
    client.list_comments(PAGE, start_cursor="cur")
    assert calls[0]["params"] == {"block_id": PAGE, "page_size": 100, "start_cursor": "cur"}


def test_comment_403_says_the_capability_is_off(notion):
    client, routes, _ = notion
    routes[("GET", "comments")] = _Resp(403, {"message": "Insufficient permissions",
                                              "code": "restricted_resource"})
    with pytest.raises(Exception, match="OFF by default"):
        client.list_comments(PAGE)


def test_edit_and_delete_comment(notion):
    client, routes, calls = notion
    routes[("PATCH", "comments/c1")] = _Resp(body={})
    routes[("DELETE", "comments/c1")] = _Resp(body={})
    client.update_comment("c1", "new")
    client.delete_comment("c1")
    assert calls[0]["json"] == {"markdown": "new"}
    assert calls[1]["method"] == "DELETE"


# --- databases ----------------------------------------------------------------

def test_create_database_adds_a_title_column(notion):
    client, routes, calls = notion
    routes[("POST", "databases")] = _Resp(body={"id": DB})
    client.create_database(PAGE, "Tasks", properties={"Due": {"date": {}}})
    body = calls[0]["json"]
    assert body["parent"] == {"type": "page_id", "page_id": PAGE}
    assert body["title"][0]["text"]["content"] == "Tasks"
    assert body["initial_data_source"]["properties"] == {
        "Name": {"title": {}}, "Due": {"date": {}}}


def test_create_typed_database(notion):
    client, routes, calls = notion
    routes[("POST", "databases")] = _Resp(body={"id": DB})
    client.create_database(PAGE, database_type="tasks")
    assert calls[0]["json"]["database_type"] == "tasks"
    assert "initial_data_source" not in calls[0]["json"]


def test_update_database_columns_go_to_the_data_source(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("PATCH", f"data_sources/{DS}")] = _Resp(body={})
    routes[("PATCH", f"databases/{DB}")] = _Resp(body={})
    client.update_database(DS, properties={"Old": None, "Due": {"name": "Deadline"}},
                           title="Renamed")
    ds_patch = [c for c in calls if c["endpoint"] == f"data_sources/{DS}" and c["method"] == "PATCH"]
    db_patch = [c for c in calls if c["endpoint"] == f"databases/{DB}"]
    assert ds_patch[0]["json"] == {"properties": {"Old": None, "Due": {"name": "Deadline"}}}
    assert db_patch[0]["json"]["title"][0]["text"]["content"] == "Renamed"


def test_update_database_with_nothing_refuses(notion):
    client, routes, _ = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    with pytest.raises(ValueError):
        client.update_database(DS)


# --- views ------------------------------------------------------------------

def test_create_view_names_database_and_data_source(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", "views")] = _Resp(body={"id": "v1"})
    client.create_view(DS, "Board", "board", sorts=[{"property": "Name", "direction": "ascending"}])
    body = _last(calls, "POST")["json"]
    assert body["database_id"] == DB and body["data_source_id"] == DS
    assert body["type"] == "board" and body["name"] == "Board"
    assert "filter" not in body


def test_create_view_rejects_unknown_type(notion):
    client, _, _ = notion
    with pytest.raises(ValueError):
        client.create_view(DS, "x", "kanban")


def test_list_views_by_database(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("GET", "views")] = _Resp(body={"results": []})
    client.list_views(DS)
    assert _last(calls, "GET")["params"] == {"database_id": DB, "page_size": 100}


def test_update_view_can_clear_filter(notion):
    client, routes, calls = notion
    routes[("PATCH", "views/v1")] = _Resp(body={})
    client.update_view("v1", name="All", clear=["filter"])
    assert calls[0]["json"] == {"name": "All", "filter": None}


def test_delete_view(notion):
    client, routes, calls = notion
    routes[("DELETE", "views/v1")] = _Resp(body={})
    client.delete_view("v1")
    assert calls[0]["method"] == "DELETE"


# --- moves, blocks, Markdown, users ------------------------------------------

def test_move_page_under_a_page(notion):
    client, routes, calls = notion
    routes[("POST", "pages/abc/move")] = _Resp(body={})
    client.move_page("abc", PAGE)
    assert calls[0]["json"] == {"parent": {"type": "page_id", "page_id": PAGE}}


def test_move_page_into_a_database_uses_its_data_source(notion):
    client, routes, calls = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": [{"id": DS}]})
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", "pages/abc/move")] = _Resp(body={})
    client.move_page("abc", DB, "database")
    assert _last(calls, "POST")["json"] == {
        "parent": {"type": "data_source_id", "data_source_id": DS}}


def test_edit_markdown_search_and_replace(notion):
    client, routes, calls = notion
    routes[("PATCH", f"pages/{PAGE}/markdown")] = _Resp(body={})
    client.edit_markdown(PAGE, replacements=[{"old_str": "a", "new_str": "b"}])
    assert calls[0]["json"] == {"type": "update_content", "update_content": {
        "content_updates": [{"old_str": "a", "new_str": "b"}],
        "allow_deleting_content": False}}


def test_edit_markdown_insert_at_start(notion):
    client, routes, calls = notion
    routes[("PATCH", f"pages/{PAGE}/markdown")] = _Resp(body={})
    client.edit_markdown(PAGE, insert="## Today", position="start")
    assert calls[0]["json"] == {"type": "insert_content", "insert_content": {
        "content": "## Today", "position": {"type": "start"}}}


def test_edit_markdown_replace_all(notion):
    client, routes, calls = notion
    routes[("PATCH", f"pages/{PAGE}/markdown")] = _Resp(body={})
    client.edit_markdown(PAGE, new_content="# New")
    assert calls[0]["json"]["replace_content"]["new_str"] == "# New"


def test_edit_markdown_needs_one_mode(notion):
    client, _, _ = notion
    with pytest.raises(ValueError):
        client.edit_markdown(PAGE)
    with pytest.raises(ValueError):
        client.edit_markdown(PAGE, insert="x", new_content="y")


def test_update_and_delete_block(notion):
    client, routes, calls = notion
    routes[("PATCH", "blocks/b1")] = _Resp(body={})
    routes[("DELETE", "blocks/b1")] = _Resp(body={})
    client.update_block("b1", {"to_do": {"checked": True}})
    client.delete_block("b1")
    assert calls[0]["json"] == {"to_do": {"checked": True}}
    assert calls[1]["method"] == "DELETE"


def test_update_page_trash_uses_in_trash(notion):
    client, routes, calls = notion
    routes[("PATCH", f"pages/{PAGE}")] = _Resp(body={})
    client.update_page(PAGE, archived=True)
    client.update_page(PAGE, in_trash=False, icon={"type": "emoji", "emoji": "📝"})
    assert calls[0]["json"] == {"in_trash": True}
    assert calls[1]["json"] == {"in_trash": False, "icon": {"type": "emoji", "emoji": "📝"}}


def test_get_blocks_follows_pagination(notion):
    client, routes, calls = notion

    def page(kw):
        if kw["params"].get("start_cursor") == "c2":
            return _Resp(body={"results": [{"id": "b3"}], "has_more": False})
        return _Resp(body={"results": [{"id": "b1"}, {"id": "b2"}],
                           "has_more": True, "next_cursor": "c2"})
    routes[("GET", f"blocks/{PAGE}/children")] = page
    result = client.get_page_blocks(PAGE)
    assert [b["id"] for b in result["results"]] == ["b1", "b2", "b3"]


def test_query_database_passes_the_cursor(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body=_schema())
    routes[("POST", f"data_sources/{DS}/query")] = _Resp(body={"results": []})
    client.query_database(DS, start_cursor="cur")
    assert _last(calls, "POST")["json"]["start_cursor"] == "cur"


def test_list_users(notion):
    client, routes, calls = notion
    routes[("GET", "users")] = _Resp(body={"results": []})
    client.list_users()
    assert calls[0]["params"] == {"page_size": 100}


# --- review fixes -------------------------------------------------------------

def test_rate_limit_is_not_misreported_as_sharing(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(429, {"message": "Rate limited",
                                                        "code": "rate_limited"})
    with pytest.raises(Exception, match="429") as err:
        client.create_page(DS, "database", "T")
    assert "not a database" not in str(err.value)
    assert [c["endpoint"] for c in calls] == [f"data_sources/{DS}"]


def test_get_database_auth_error_surfaces(notion):
    client, routes, calls = notion
    routes[("GET", f"databases/{DB}")] = _Resp(401, {"message": "bad token",
                                                     "code": "unauthorized"})
    with pytest.raises(Exception, match="401"):
        client.get_database(DB)
    assert len(calls) == 1


def test_database_with_several_data_sources_asks_which(notion):
    client, routes, calls = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": [
        {"id": "a1", "name": "2025"}, {"id": "b2", "name": "2026"}]})
    with pytest.raises(ValueError, match="2 data sources"):
        client.create_page(DB, "database", "T")
    assert not [c for c in calls if c["endpoint"] == "pages"]


def test_synced_data_source_lists_views_by_data_source(notion):
    client, routes, calls = notion
    routes[("GET", f"data_sources/{DS}")] = _Resp(body={
        "id": DS, "parent": {"type": "data_source_id", "data_source_id": "x"},
        "properties": {}})
    routes[("GET", "views")] = _Resp(body={"results": []})
    client.list_views(DS)
    assert _last(calls, "GET")["params"] == {"data_source_id": DS, "page_size": 100}


def test_update_view_refuses_set_and_clear(notion):
    client, _, _ = notion
    with pytest.raises(ValueError):
        client.update_view("v1", filter_obj={"x": 1}, clear=["filter"])


def test_recursive_blocks_skip_child_pages(notion):
    client, routes, calls = notion
    routes[("GET", f"blocks/{PAGE}/children")] = _Resp(body={"results": [
        {"id": "sub", "type": "child_page", "has_children": True},
        {"id": "tog", "type": "toggle", "has_children": True}], "has_more": False})
    routes[("GET", "blocks/tog/children")] = _Resp(body={"results": [], "has_more": False})
    client.get_page_blocks(PAGE, recursive=True)
    assert [c["endpoint"] for c in calls] == [f"blocks/{PAGE}/children", "blocks/tog/children"]
