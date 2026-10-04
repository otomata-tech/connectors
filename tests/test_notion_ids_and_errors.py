"""Every id is checked before it enters a Notion API path; the errors that
guide the caller keep a name and a 400 status; a non-JSON answer is an upstream
error, not a bad argument; block reads are bounded. Stub of `requests.request`:
no real call to Notion."""
from __future__ import annotations

import pytest

from oto.tools.notion.lib import notion_client as nc
from oto.tools.notion.lib._ids import notion_id

from notion_fake import DB, DS, PAGE, _Resp, notion  # noqa: F401

DASHED = "33333333-3333-3333-3333-333333333333"
FORGED = ["../pages/" + PAGE, PAGE + "/../../users", "123?x=y", PAGE + "?a=b",
          "", "abc", "g" * 32]


# --- notion_id ----------------------------------------------------------------

def test_accepts_bare_dashed_and_url_forms():
    assert notion_id(PAGE) == PAGE
    assert notion_id(DASHED) == PAGE
    assert notion_id(PAGE.upper()) == PAGE
    assert notion_id(f"https://www.notion.so/ws/Roadmap-{PAGE}?v={DB}") == PAGE
    assert notion_id(f"https://www.notion.so/{PAGE}") == PAGE


@pytest.mark.parametrize("bad", FORGED)
def test_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        notion_id(bad, "page_id")


@pytest.mark.parametrize("call", [
    lambda c, x: c.get_page(x),
    lambda c, x: c.get_page_blocks(x),
    lambda c, x: c.update_page(x, in_trash=True),
    lambda c, x: c.append_blocks(x, [{"type": "paragraph"}]),
    lambda c, x: c.query_data_source(x),
    lambda c, x: c.get_database(x),
    lambda c, x: c.query_database(x),
    lambda c, x: c.get_view(x),
    lambda c, x: c.delete_view(x),
    lambda c, x: c.update_view(x, name="n"),
    lambda c, x: c.update_comment(x, "t"),
    lambda c, x: c.delete_comment(x),
    lambda c, x: c.list_comments(x),
    lambda c, x: c.move_page(x, PAGE),
    lambda c, x: c.get_markdown(x),
    lambda c, x: c.edit_markdown(x, insert="t"),
    lambda c, x: c.update_block(x, {"to_do": {"checked": True}}),
    lambda c, x: c.delete_block(x),
])
def test_a_forged_id_never_reaches_a_request(notion, call):
    client, _, calls = notion
    with pytest.raises(ValueError):
        call(client, "../pages/" + PAGE)
    assert calls == [], "the path was built and sent anyway"


# --- named errors -------------------------------------------------------------

def test_a_page_id_given_as_database_says_so(notion):
    client, routes, _ = notion
    routes[("GET", f"data_sources/{PAGE}")] = _Resp(404, {"message": "not found"})
    routes[("GET", f"databases/{PAGE}")] = _Resp(
        400, {"message": f"Provided ID {PAGE} is a page, not a database."})
    with pytest.raises(nc.NotionIdKindError) as exc:
        client.query_database(PAGE)
    assert exc.value.status == 400 and isinstance(exc.value, ValueError)
    assert "is a PAGE" in str(exc.value)


def test_several_data_sources_are_listed(notion):
    client, routes, _ = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": [
        {"id": DS, "name": "2025"}, {"id": PAGE, "name": "2026"}]})
    with pytest.raises(nc.NotionIdKindError) as exc:
        client.query_database(DB)
    assert DS in str(exc.value) and PAGE in str(exc.value)


def test_no_visible_data_source_is_named(notion):
    client, routes, _ = notion
    routes[("GET", f"databases/{DB}")] = _Resp(body={"id": DB, "data_sources": []})
    with pytest.raises(nc.NotionIdKindError, match="no data source"):
        client.query_database(DB)


# --- non-JSON answers ---------------------------------------------------------

class _Html(_Resp):
    def __init__(self, status):
        super().__init__(status)
        self.text = "<html><body>Bad gateway</body></html>"

    def json(self):
        raise ValueError("Expecting value")


def test_an_html_error_page_is_an_upstream_error(notion):
    client, routes, _ = notion
    routes[("GET", f"pages/{PAGE}")] = _Html(503)
    with pytest.raises(nc.NotionAPIError) as exc:
        client.get_page(PAGE)
    assert exc.value.status == 503 and not isinstance(exc.value, ValueError)


def test_a_non_json_success_is_an_upstream_error(notion):
    client, routes, _ = notion
    routes[("GET", f"pages/{PAGE}")] = _Html(200)
    with pytest.raises(nc.NotionAPIError) as exc:
        client.get_page(PAGE)
    assert exc.value.status == 502


# --- bounded block reads ------------------------------------------------------

def test_a_repeated_cursor_stops_the_read(notion):
    client, routes, _ = notion
    routes[("GET", f"blocks/{PAGE}/children")] = _Resp(
        body={"results": [{"id": DS}], "has_more": True, "next_cursor": "same"})
    with pytest.raises(nc.NotionAPIError, match="same cursor"):
        client.get_page_blocks(PAGE)


def test_a_long_page_stops_at_the_page_cap(notion, monkeypatch):
    client, routes, calls = notion
    monkeypatch.setattr(nc, "_MAX_BLOCK_PAGES", 3)
    count = {"n": 0}

    def page(kw):
        count["n"] += 1
        return _Resp(body={"results": [{"id": DS}], "has_more": True,
                           "next_cursor": f"c{count['n']}"})

    routes[("GET", f"blocks/{PAGE}/children")] = page
    result = client.get_page_blocks(PAGE)
    assert len(calls) == 3 and result["truncated"] is True
    assert result["has_more"] is True and result["next_cursor"]


def test_nesting_beyond_the_depth_cap_is_marked_not_fetched(notion, monkeypatch):
    client, routes, calls = notion
    monkeypatch.setattr(nc, "_MAX_BLOCK_DEPTH", 1)
    routes[("GET", f"blocks/{PAGE}/children")] = _Resp(body={"results": [
        {"id": DS, "type": "toggle", "has_children": True}], "has_more": False})
    result = client.get_page_blocks(PAGE, recursive=True)
    assert len(calls) == 1
    assert result["results"][0]["children_truncated"] is True

