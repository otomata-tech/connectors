"""L'aide commune `require` : le secret est fourni par le consommateur, ou c'est une erreur nommée."""
import pytest

from oto.tools.common.credentials import MissingCredential, require


def test_une_valeur_fournie_est_rendue_telle_quelle():
    assert require("sk-123", "SERPER_API_KEY") == "sk-123"
    creds = {"client_email": "x@y"}
    assert require(creds, "GA4_SERVICE_ACCOUNT_JSON") is creds


@pytest.mark.parametrize("absente", [None, ""])
def test_une_valeur_absente_leve_une_erreur_nommee(absente):
    with pytest.raises(MissingCredential) as e:
        require(absente, "SERPER_API_KEY")
    assert e.value.name == "SERPER_API_KEY"
    assert "SERPER_API_KEY" in str(e.value)
    assert "consumer" in str(e.value)


def test_l_erreur_reste_une_valueerror():
    """Les appelants qui attrapaient la `ValueError` de l'ancienne résolution la voient encore."""
    assert issubclass(MissingCredential, ValueError)


def test_l_environnement_n_est_jamais_lu(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "depuis-l-env")
    from oto.tools.serper.client import SerperClient
    with pytest.raises(MissingCredential):
        SerperClient()
