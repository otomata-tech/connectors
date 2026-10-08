"""Append Sheets : ancré sur la première colonne de la plage demandée.

Google ajoute après la dernière « table » qu'il détecte dans la plage, à partir de
la première colonne de cette table : sur `Feuille!A:R`, un bloc de données qui
commence en colonne R envoie la ligne en R…AI au lieu de A…R. L'appel est donc
ancré sur la seule première colonne, et la plage écrite est contrôlée.
"""
from unittest.mock import MagicMock, patch

import pytest

from oto.tools.google.sheets.lib.sheets_client import (
    SheetsClient, SheetsClientError, _append_anchor)


@pytest.mark.parametrize("demandee, ancre, colonne", [
    ("'Les 90 posts'!A:R", "'Les 90 posts'!A:A", "A"),
    ("Feuille!A1:R", "Feuille!A1:A", "A"),
    ("Feuille!C5:F", "Feuille!C5:C", "C"),
    ("Feuille", "Feuille!A:A", "A"),
    ("A:R", "A:A", "A"),
    ("feuille!b:d", "feuille!B:B", "B"),
])
def test_l_ancre_est_la_premiere_colonne_de_la_plage(demandee, ancre, colonne):
    assert _append_anchor(demandee) == (ancre, colonne)


def _client(updated_range):
    with patch("oto.tools.google.sheets.lib.sheets_client.build") as build:
        client = SheetsClient(credentials=object())
    appel = client.sheets.values.return_value.append
    appel.return_value.execute.return_value = {
        "updates": {"updatedRange": updated_range, "updatedRows": 1, "updatedCells": 18}}
    return client, appel


def test_l_append_part_sur_la_plage_ancree():
    client, appel = _client("'Les 90 posts'!A118:R118")
    out = client.append("sid", "'Les 90 posts'!A:R", [["x"] * 18])
    assert appel.call_args.kwargs["range"] == "'Les 90 posts'!A:A"
    assert appel.call_args.kwargs["insertDataOption"] == "INSERT_ROWS"
    assert out["updated_range"] == "'Les 90 posts'!A118:R118"


def test_une_ligne_ecrite_dans_d_autres_colonnes_leve():
    client, _ = _client("'Les 90 posts'!R118:AI118")
    with pytest.raises(SheetsClientError, match="R118:AI118.*column A"):
        client.append("sid", "'Les 90 posts'!A:R", [["x"] * 18])
