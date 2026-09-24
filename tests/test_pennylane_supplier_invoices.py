"""Factures d'achat Pennylane : les chemins et les corps exacts que l'API attend.

Les épreuves citent les chemins en clair (référence API v2 `supplier_invoices`) :
la validation vit sous `/validate_accounting`, sans corps, et la correction des
lignes passe par un objet `{create|update|delete}`, pas par une liste.
"""
import pytest

from oto.tools.pennylane.client import PennylaneClient


def _client():
    """Un client dont le transport est remplacé par un mouchard."""
    c = PennylaneClient(api_key="k", rate_limit_delay=0)
    c.appels = []

    def _appel(methode, endpoint, *, params=None, data=None, retries=3):
        c.appels.append((methode, endpoint, data))
        return {"id": 7, "accounting_status": "complete"}
    c._appel = _appel

    def _pages(endpoint, params=None, max_pages=None, per_page=100):
        c.appels.append(("PAGES", endpoint, params))
        return [{"id": 1, "vat_rate": "exempt"}]
    c.fetch_all_pages = _pages
    return c


def test_validate_accounting_est_un_put_sans_corps_sur_le_bon_chemin():
    c = _client()
    r = c.validate_supplier_invoice_accounting(42)
    assert c.appels == [("PUT", "supplier_invoices/42/validate_accounting", None)]
    assert r["accounting_status"] == "complete"


def test_get_et_lignes_lisent_la_facture_puis_sa_sous_ressource_paginee():
    c = _client()
    c.get_supplier_invoice(42)
    lignes = c.get_supplier_invoice_lines(42)
    assert c.appels[0] == ("GET", "supplier_invoices/42", None)
    assert c.appels[1][:2] == ("PAGES", "supplier_invoices/42/invoice_lines")
    assert lignes == [{"id": 1, "vat_rate": "exempt"}]


def test_update_pose_le_vat_rate_d_une_ligne_existante_et_le_libelle():
    c = _client()
    c.update_supplier_invoice(
        42, fields={"label": "Hébergement"},
        invoice_lines={"update": [{"id": 9, "vat_rate": "intracom_100"}]})
    verbe, endpoint, corps = c.appels[0]
    assert (verbe, endpoint) == ("PUT", "supplier_invoices/42")
    assert corps == {"label": "Hébergement",
                     "invoice_lines": {"update": [{"id": 9, "vat_rate": "intracom_100"}]}}


@pytest.mark.parametrize("lignes, motif", [
    ([{"id": 9}], "objet"),
    ({"modifier": [{"id": 9}]}, "inconnue"),
    ({"update": [{"vat_rate": "exempt"}]}, "id"),
    ({"delete": [{}]}, "id"),
])
def test_update_refuse_une_forme_de_lignes_que_l_api_rejetterait(lignes, motif):
    c = _client()
    with pytest.raises(ValueError, match=motif):
        c.update_supplier_invoice(42, invoice_lines=lignes)
    assert c.appels == []


def test_update_refuse_une_modification_vide():
    c = _client()
    with pytest.raises(ValueError, match="rien à modifier"):
        c.update_supplier_invoice(42)
    assert c.appels == []


def test_import_reste_sur_supplier_invoices_import():
    c = _client()
    c.import_supplier_invoice(
        file_attachment_id=5, supplier_id=3, date="2026-09-01", deadline="2026-10-01",
        currency_amount_before_tax="100.00", currency_amount="120.00",
        currency_tax="20.00",
        invoice_lines=[{"currency_amount": "120.00", "currency_tax": "20.00",
                        "vat_rate": "FR_200"}],
        import_as_incomplete=True)
    verbe, endpoint, corps = c.appels[0]
    assert (verbe, endpoint) == ("POST", "supplier_invoices/import")
    assert corps["import_as_incomplete"] is True
    assert "currency_amount_before_tax" not in corps["invoice_lines"][0]
