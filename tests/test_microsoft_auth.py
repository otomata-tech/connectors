"""Auth Microsoft — verrouille le contrat du serveur d'autorisation Entra.

Cible ce qui pourrait dériver en silence : les secrets dans le corps et jamais
dans l'URL, les scopes toujours nommés par l'appelant, le renouvellement sur
`.default`, l'annuaire visé (`tenant`), la rotation du refresh token, le
classement « autorisation morte » contre « configuration fausse », et l'URL de
consentement administrateur.
"""
from urllib.parse import parse_qs, urlsplit

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.microsoft import MicrosoftAuthError, MicrosoftGrantExpired, scopes
from oto.tools.microsoft import auth as ms_auth
from microsoft_fake import CID, REDIRECT, SECRET, _Resp, token_calls  # noqa: F401

LOGIN = "https://login.microsoftonline.com"
TOKEN_URL = f"{LOGIN}/organizations/oauth2/v2.0/token"
SCOPES = scopes.IDENTITY + scopes.FILES + scopes.MAIL


def _split(url):
    parts = urlsplit(url)
    return (f"{parts.scheme}://{parts.netloc}{parts.path}",
            {k: v[0] for k, v in parse_qs(parts.query).items()})


def test_url_d_autorisation():
    base, q = _split(ms_auth.authorize_url(CID, REDIRECT, "etat-signe", scopes=SCOPES))
    assert base == f"{LOGIN}/organizations/oauth2/v2.0/authorize"
    assert q == {"client_id": CID, "response_type": "code", "redirect_uri": REDIRECT,
                 "response_mode": "query", "scope": " ".join(SCOPES),
                 "state": "etat-signe", "prompt": "select_account"}


def test_scopes_obligatoires():
    with pytest.raises(TypeError):
        ms_auth.authorize_url(CID, REDIRECT, "s")
    with pytest.raises(TypeError):
        ms_auth.exchange_code(CID, SECRET, "c", REDIRECT)
    with pytest.raises(ValueError, match="scopes"):
        ms_auth.authorize_url(CID, REDIRECT, "s", scopes=())


@pytest.mark.parametrize("tenant", ["0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
                                    "contoso.onmicrosoft.com", "contoso.com"])
def test_url_par_tenant(tenant, token_calls):
    base, _ = _split(ms_auth.authorize_url(CID, REDIRECT, "s", scopes=SCOPES, tenant=tenant))
    assert base == f"{LOGIN}/{tenant}/oauth2/v2.0/authorize"
    ms_auth.exchange_code(CID, SECRET, "c", REDIRECT, scopes=SCOPES, tenant=tenant)
    ms_auth.refresh(CID, SECRET, "RT", tenant=tenant)
    assert [c["url"] for c in token_calls] == [f"{LOGIN}/{tenant}/oauth2/v2.0/token"] * 2
    base, _ = _split(ms_auth.admin_consent_url(CID, REDIRECT, "s", scopes=scopes.TEAMS_ADMIN,
                                               tenant=tenant))
    assert base == f"{LOGIN}/{tenant}/v2.0/adminconsent"


@pytest.mark.parametrize("tenant", ["common", "Consumers"])
def test_tenant_sans_annuaire_d_entreprise_refuse(tenant, token_calls):
    with pytest.raises(ValueError, match="organization's directory"):
        ms_auth.authorize_url(CID, REDIRECT, "s", scopes=SCOPES, tenant=tenant)
    with pytest.raises(ValueError, match="organization's directory"):
        ms_auth.refresh(CID, SECRET, "RT", tenant=tenant)
    assert token_calls == []


@pytest.mark.parametrize("tenant", ["", "a/b", "contoso.com?x=1", "a#b", "con toso",
                                    "../common", ".contoso.com"])
def test_tenant_mal_forme_refuse(tenant, token_calls):
    with pytest.raises(ValueError, match="tenant"):
        ms_auth.exchange_code(CID, SECRET, "c", REDIRECT, scopes=SCOPES, tenant=tenant)
    assert token_calls == []


def test_echange_du_code_secret_dans_le_corps(token_calls):
    grant = ms_auth.exchange_code(CID, SECRET, "le-code", REDIRECT, scopes=SCOPES)
    call = token_calls[0]
    assert call["url"] == TOKEN_URL and "params" not in call
    assert SECRET not in call["url"]
    assert call["data"] == {"grant_type": "authorization_code", "client_id": CID,
                            "client_secret": SECRET, "code": "le-code",
                            "redirect_uri": REDIRECT, "scope": " ".join(SCOPES)}
    assert (grant.access_token, grant.refresh_token, grant.expires_in) == ("AT", "RT2", 3599)


def test_renouvellement_par_defaut_sur_default(token_calls):
    grant = ms_auth.refresh(CID, SECRET, "RT1")
    data = token_calls[0]["data"]
    assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "RT1"
    assert data["scope"] == "https://graph.microsoft.com/.default offline_access"
    assert token_calls[0]["url"] == TOKEN_URL and "params" not in token_calls[0]
    assert grant.refresh_token == "RT2"


def test_renouvellement_sans_rotation_garde_l_ancien(token_calls):
    token_calls.responses.append(_Resp({"access_token": "AT", "expires_in": 60}))
    assert ms_auth.refresh(CID, SECRET, "RT1").refresh_token == "RT1"


def test_autorisation_morte_classee_a_part(token_calls):
    token_calls.responses.append(_Resp({
        "error": "invalid_grant",
        "error_description": "AADSTS70008: The refresh token has expired.\r\nTrace ID: x"},
        status_code=400))
    with pytest.raises(MicrosoftGrantExpired) as exc:
        ms_auth.refresh(CID, SECRET, "RT1")
    assert exc.value.code == "AADSTS70008" and "Trace ID" not in str(exc.value)
    assert exc.value.status_code == 400


def test_interaction_requise_est_une_autorisation_morte(token_calls):
    token_calls.responses.append(_Resp({
        "error": "interaction_required",
        "error_description": "AADSTS50076: MFA required."}, status_code=400))
    with pytest.raises(MicrosoftGrantExpired) as exc:
        ms_auth.refresh(CID, SECRET, "RT1")
    assert exc.value.code == "AADSTS50076"


def test_secret_d_application_faux_n_est_pas_une_autorisation_morte(token_calls):
    token_calls.responses.append(_Resp({
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided."},
        status_code=401))
    with pytest.raises(MicrosoftAuthError) as exc:
        ms_auth.refresh(CID, SECRET, "RT1")
    assert not isinstance(exc.value, MicrosoftGrantExpired)
    assert exc.value.code == "AADSTS7000215" and SECRET not in str(exc.value)


@pytest.mark.parametrize("fn, nom", [
    (lambda: ms_auth.refresh(CID, "", "RT"), "MICROSOFT_CLIENT_SECRET"),
    (lambda: ms_auth.refresh(CID, SECRET, ""), "MICROSOFT_REFRESH_TOKEN"),
    (lambda: ms_auth.admin_consent_url("", REDIRECT, "s", scopes=scopes.TEAMS_ADMIN),
     "MICROSOFT_CLIENT_ID"),
])
def test_credential_manquant_nomme(fn, nom):
    with pytest.raises(MissingCredential) as exc:
        fn()
    assert exc.value.name == nom


# --- consentement administrateur --------------------------------------------------

def test_consentement_admin_sans_scopes_oidc_et_en_uri():
    url = ms_auth.admin_consent_url(CID, "https://oto.example/cb?x=1&y=2", "é tat",
                                    scopes=scopes.IDENTITY + ("openid",) + scopes.TEAMS_ADMIN)
    base, q = _split(url)
    assert base == f"{LOGIN}/organizations/v2.0/adminconsent"
    assert q == {"client_id": CID, "redirect_uri": "https://oto.example/cb?x=1&y=2",
                 "state": "é tat",
                 "scope": "https://graph.microsoft.com/User.Read "
                          "https://graph.microsoft.com/ChannelMessage.Read.All"}
    assert "offline_access" not in url and "openid" not in url
    assert "y=2&" not in url  # le redirect_uri est encodé, pas recopié


def test_consentement_admin_sans_permission_graph_refuse():
    with pytest.raises(ValueError, match="scopes"):
        ms_auth.admin_consent_url(CID, REDIRECT, "s", scopes=("offline_access", "openid"))
