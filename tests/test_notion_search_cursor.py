"""`search` repasse le curseur de Notion (otomata-tech/oto#249).

`POST /search` rend au plus 100 objets, avec `has_more` et `next_cursor`. Sans
`start_cursor`, un appelant ne voyait JAMAIS que les 100 premiers : mesuré le
04/09/2026, une requête vide triée par date d'édition ne remontait qu'à la
mi-journée, le reste de la journée restait hors d'atteinte. Doublure de
`requests.request` : aucun appel réel à Notion.
"""
from __future__ import annotations

import pytest

from oto.tools.notion.lib import notion_client as nc


class _Resp:
    status_code = 200
    headers: dict = {}
    text = "{}"

    def json(self):
        return {"results": [], "has_more": False}

    def raise_for_status(self):
        pass


@pytest.fixture()
def envoye(monkeypatch):
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(nc.requests, "request", fake_request)
    return calls


def test_le_curseur_part_dans_le_corps(envoye):
    nc.NotionClient(token="t", cache_enabled=False).search(
        "", sort="last_edited_time", start_cursor="cur-2")
    corps = envoye[0]["json"]
    assert corps["start_cursor"] == "cur-2"
    assert corps["sort"] == {"direction": "descending", "timestamp": "last_edited_time"}


def test_sans_curseur_le_corps_ne_change_pas(envoye):
    nc.NotionClient(token="t", cache_enabled=False).search("roadmap", filter_type="page")
    assert envoye[0]["json"] == {
        "query": "roadmap", "filter": {"value": "page", "property": "object"}}
