"""Écritures de SCHÉMA Attio : attribut, option de sélection, étape (otomata-tech/oto#256).

On juge sur le fil (verbe, chemin, corps), jamais à la lecture du code.
"""
from __future__ import annotations

import pytest

from oto.tools.attio import client as ac


@pytest.fixture()
def wire(monkeypatch):
    seen = {}

    def fake_request(self, method, endpoint, **kwargs):
        seen.update(method=method, endpoint=endpoint, **kwargs)
        return {"data": {}}

    monkeypatch.setattr(ac.AttioClient, "_request", fake_request)
    return seen


def _attributes():
    return ac.AttioClient(api_key="attio-test-key").attributes


def test_create_poste_la_definition_telle_quelle(wire):
    definition = {"title": "Source", "description": None, "api_slug": "source",
                  "type": "select", "is_required": False, "is_unique": False,
                  "is_multiselect": False, "config": {}}
    _attributes().create("objects", "companies", definition)
    assert wire["method"] == "POST"
    assert wire["endpoint"] == "objects/companies/attributes"
    assert wire["json"] == {"data": definition}


def test_create_option_vise_l_attribut(wire):
    _attributes().create_option("lists", "pipeline", "source", "Salon")
    assert wire["method"] == "POST"
    assert wire["endpoint"] == "lists/pipeline/attributes/source/options"
    assert wire["json"] == {"data": {"title": "Salon"}}


def test_create_status_vise_l_attribut(wire):
    _attributes().create_status("objects", "deals", "stage", "Négociation")
    assert wire["method"] == "POST"
    assert wire["endpoint"] == "objects/deals/attributes/stage/statuses"
    assert wire["json"] == {"data": {"title": "Négociation"}}
