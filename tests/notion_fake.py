"""Stub of `requests.request` for the Notion client tests: routes by
(method, endpoint) and records every call. No real call to Notion."""
from __future__ import annotations

import pytest
import requests

from oto.tools.notion.lib import notion_client as nc

DS = "11111111111111111111111111111111"
DB = "22222222222222222222222222222222"
PAGE = "33333333333333333333333333333333"
# Ids of other objects (comments, views, blocks, created pages): real Notion
# ids are 32 hex, and the client refuses anything else before the request.
NEW = "44444444444444444444444444444444"
C1 = "55555555555555555555555555555555"
V1 = "66666666666666666666666666666666"
B1 = "77777777777777777777777777777777"
SUB = "88888888888888888888888888888888"
TOG = "99999999999999999999999999999999"
MOVED = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = "x"
        self.headers: dict = {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


@pytest.fixture()
def notion(monkeypatch):
    """Routes requests by (method, endpoint) -> response; records each call."""
    routes: dict = {}
    calls: list = []

    def fake_request(method, url, **kwargs):
        endpoint = url.split("/v1/", 1)[1]
        calls.append({"method": method, "endpoint": endpoint, **kwargs})
        resp = routes.get((method, endpoint))
        if callable(resp):
            resp = resp(kwargs)
        return resp or _Resp(404, {"message": "Could not find object",
                                   "code": "object_not_found"})

    monkeypatch.setattr(nc.requests, "request", fake_request)
    client = nc.NotionClient(token="t", cache_enabled=False)
    return client, routes, calls
