"""Devis Pennylane : les chemins et les corps exacts que l'API attend.

Les épreuves citent les chemins en clair (référence API v2 `quotes`) pour qu'un
renommage se voie ici et pas en production — en particulier la conversion,
qui vit sous `customer_invoices/`, pas sous `quotes/`.
"""
import json

import pytest

from oto.tools.pennylane.client import PennylaneClient

LIGNE = {"label": "Accompagnement", "quantity": 2, "unit": "day",
         "raw_currency_unit_price": "700.00", "vat_rate": "FR_200"}
REMISE = {"label": "Remise", "quantity": 1, "unit": "piece",
          "raw_currency_unit_price": "-100.00", "vat_rate": "FR_200"}


def _client():
    """Un client dont le transport est remplacé par un mouchard."""
    c = PennylaneClient(api_key="k", rate_limit_delay=0)
    c.appels = []

    def _mouchard(verbe):
        def _f(endpoint, data=None, **kw):
            c.appels.append((verbe, endpoint, data))
            return {"id": 7}
        return _f

    c.post = _mouchard("POST")
    c.put = _mouchard("PUT")

    def _pages(endpoint, params=None, max_pages=None, per_page=100):
        c.appels.append(("PAGES", endpoint, params))
        return []
    c.fetch_all_pages = _pages

    def _un(endpoint, params=None, retries=3):
        c.appels.append(("GET", endpoint, params))
        return {"items": [{"id": 1}]}
    c.fetch = _un
    return c


def test_create_quote_poste_sur_quotes_avec_les_lignes_et_le_texte_libre():
    c = _client()
    c.create_quote(12, "2026-09-24", "2026-10-24", [LIGNE, REMISE],
                   external_reference="DEV-1", pdf_free_text="Valable 30 jours",
                   quote_template_id=3)
    verbe, endpoint, corps = c.appels[0]
    assert (verbe, endpoint) == ("POST", "quotes")
    assert corps["customer_id"] == 12 and corps["deadline"] == "2026-10-24"
    assert corps["invoice_lines"] == [LIGNE, REMISE]  # la remise négative passe telle quelle
    assert corps["pdf_invoice_free_text"] == "Valable 30 jours"
    assert corps["quote_template_id"] == 3
    assert corps["external_reference"] == "DEV-1"
    assert "draft" not in corps  # un devis n'a pas de brouillon


def test_create_quote_sans_option_n_envoie_pas_de_champ_vide():
    c = _client()
    c.create_quote(12, "2026-09-24", "2026-10-24", [LIGNE])
    corps = c.appels[0][2]
    for champ in ("pdf_invoice_free_text", "quote_template_id", "external_reference"):
        assert champ not in corps


def test_list_quotes_filtre_serveur_statut_et_client():
    c = _client()
    c.list_quotes(max_pages=2, status="accepted", customer_id=12)
    verbe, endpoint, params = c.appels[0]
    assert (verbe, endpoint) == ("PAGES", "quotes")
    assert json.loads(params["filter"]) == [
        {"field": "status", "operator": "eq", "value": "accepted"},
        {"field": "customer_id", "operator": "eq", "value": "12"}]


def test_list_quotes_sans_filtre():
    c = _client()
    c.list_quotes()
    assert c.appels[0] == ("PAGES", "quotes", None)


def test_un_statut_inconnu_est_refuse_avant_l_appel():
    c = _client()
    with pytest.raises(ValueError, match="statut de devis inconnu"):
        c.list_quotes(status="draft")
    with pytest.raises(ValueError, match="statut de devis inconnu"):
        c.update_quote_status(7, "signed")
    assert c.appels == []


def test_get_quote_et_ses_lignes():
    c = _client()
    c.get_quote(7)
    assert c.appels[-1][:2] == ("GET", "quotes/7")
    assert c.get_quote_lines(7) == [{"id": 1}]
    assert c.appels[-1][:2] == ("GET", "quotes/7/invoice_lines")


def test_update_quote_status_vise_update_status():
    c = _client()
    c.update_quote_status(7, "accepted")
    assert c.appels[0] == ("PUT", "quotes/7/update_status", {"status": "accepted"})


def test_la_conversion_vit_sous_customer_invoices_et_part_en_brouillon():
    c = _client()
    c.create_invoice_from_quote(7, customer_invoice_template_id=4)
    verbe, endpoint, corps = c.appels[0]
    assert (verbe, endpoint) == ("POST", "customer_invoices/create_from_quote")
    assert corps == {"quote_id": 7, "draft": True, "customer_invoice_template_id": 4}
