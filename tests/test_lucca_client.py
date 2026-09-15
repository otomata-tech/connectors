"""LuccaClient — construction (secrets requis, URL par tenant), header d'auth
(JAMAIS en query string), appels de liste paginés, appel détail par id, et
traduction d'un refus HTTP amont (401/404/429).

Mocke `requests.Session.get` : contrat HTTP vérifié (URL, params, headers)
sans réseau ni clé réelle. Domaine de test factice ("acme"), jamais un tenant
réel.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.lucca import client as lucca_client
from oto.tools.lucca.client import LuccaClient


class _Resp:
    def __init__(self, payload, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class _Stub:
    """field_filter no-op (évite la lecture de ~/.otomata/config.yaml)."""

    def apply(self, x):
        return x


@pytest.fixture
def transport(monkeypatch):
    """Capture chaque appel `Session.get` ; une réponse mise en file via
    `responses.append(...)` sert la PROCHAINE requête, sinon une liste par défaut."""
    captured = []
    responses = []

    def fake_get(self, url, headers=None, params=None, timeout=None):
        captured.append({"url": url, "headers": headers, "params": params})
        if responses:
            return responses.pop(0)
        return _Resp({"data": {"items": [{"id": 1}], "count": 1}})

    monkeypatch.setattr(lucca_client.requests.Session, "get", fake_get)
    return captured, responses


@pytest.fixture
def c():
    return LuccaClient(api_key="test-key", domain="acme", field_filter=_Stub())


# --- Construction : secrets requis, URL par tenant ------------------------

def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("LUCCA_API_KEY", raising=False)
    monkeypatch.setenv("LUCCA_DOMAIN", "acme")
    monkeypatch.setenv("OTO_CONFIG_DISABLE_SOPS", "1")
    with pytest.raises(ValueError):
        LuccaClient()


def test_requires_domain(monkeypatch):
    monkeypatch.setenv("LUCCA_API_KEY", "test-key")
    monkeypatch.delenv("LUCCA_DOMAIN", raising=False)
    monkeypatch.setenv("OTO_CONFIG_DISABLE_SOPS", "1")
    with pytest.raises(ValueError):
        LuccaClient()


def test_builds_tenant_base_url_from_domain_alone(c):
    # LUCCA_DOMAIN porte le SOUS-DOMAINE seul ("acme"), pas une URL complète —
    # le client construit https://{domain}.ilucca.net lui-même.
    assert c.base_url == "https://acme.ilucca.net"


def test_different_tenants_get_different_urls():
    other = LuccaClient(api_key="k", domain="exemple", field_filter=_Stub())
    assert other.base_url == "https://exemple.ilucca.net"


# --- Auth header, jamais en query string -----------------------------------

def test_auth_header_correctly_posed(c, transport):
    calls, _ = transport
    c.list_users()
    call = calls[-1]
    assert call["headers"]["Authorization"] == "lucca application=test-key"
    # PAS de préfixe Bearer, PAS d'OAuth2 — un header brut, tel quel.


def test_api_key_never_leaks_into_query_params(c, transport):
    calls, _ = transport
    c.list_users(mail="jean.dupont@exemple.fr")
    call = calls[-1]
    for v in (call["params"] or {}).values():
        assert "test-key" not in str(v)
    assert "test-key" not in call["url"]


# --- Listing paginé ---------------------------------------------------------

def test_list_users_sends_paging_and_url(c, transport):
    calls, _ = transport
    c.list_users(offset=20, limit=50)
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/users"
    assert call["params"]["paging"] == "20,50"


def test_list_leaves_requires_date_and_sends_paging(c, transport):
    calls, _ = transport
    c.list_leaves(date="between,2026-09-01,2026-09-30", owner_id=[42])
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/leaves"
    assert call["params"]["date"] == "between,2026-09-01,2026-09-30"
    assert call["params"]["paging"] == "0,1000"
    assert call["params"]["leavePeriod.ownerId"] == [42]


def test_list_leaves_needs_a_date_argument(c):
    with pytest.raises(TypeError):
        c.list_leaves()  # date est positionnel requis, pas de "tout lister"


def test_paging_rejects_limit_over_1000(c):
    with pytest.raises(ValueError):
        c.list_users(limit=1001)


def test_list_leave_requests_sends_no_query_params(c, transport):
    # Vérifié sur la doc Lucca : GET /api/v3/leaveRequests ne documente AUCUN
    # paramètre de requête, pas même `paging` — contrairement à tous les
    # autres endpoints de liste de ce client.
    calls, _ = transport
    c.list_leave_requests()
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/leaveRequests"
    assert not call["params"]


def test_list_expense_claims_sends_paging_and_filters(c, transport):
    calls, _ = transport
    c.list_expense_claims(status_id="Approved", order_by="declaredOn,desc")
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/expenseClaims"
    assert call["params"]["paging"] == "0,1000"
    assert call["params"]["statusId"] == "Approved"
    assert call["params"]["orderBy"] == "declaredOn,desc"


def test_no_get_expense_claim_by_id_method(c):
    # Aucun GET /api/v3/expenseClaims/{id} dans la doc legacy v3 (vérifié) —
    # ce client ne doit donc PAS promettre une méthode qui n'existerait pas
    # côté Lucca.
    assert not hasattr(c, "get_expense_claim")


def test_list_departments_sends_paging(c, transport):
    calls, _ = transport
    c.list_departments(head_id=7)
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/departments"
    assert call["params"]["paging"] == "0,1000"
    assert call["params"]["headId"] == 7


def test_list_establishments_uses_org_structure_path_and_page_limit(c, transport):
    # Endpoint DIFFÉRENT du reste : pas /api/v3/..., pagination page/limit au
    # lieu de paging, et enveloppe de réponse sans clé "data".
    calls, responses = transport
    responses.append(_Resp({"items": [{"id": 7}], "prev": None, "next": None}))
    result = c.list_establishments(page=2, limit=25, is_archived=False)
    call = calls[-1]
    assert call["url"] == (
        "https://acme.ilucca.net/organization/structure/api/establishments"
    )
    assert call["params"]["page"] == 2
    assert call["params"]["limit"] == 25
    # bool Python normalisé en chaîne minuscule, jamais "False".
    assert call["params"]["isArchived"] == "false"
    assert result == [{"id": 7}]


# --- Appel détail par id -----------------------------------------------------

def test_get_user_by_id(c, transport):
    calls, responses = transport
    responses.append(_Resp({"data": {"id": 42, "firstName": "Jean"}}))
    result = c.get_user(42, fields="id,firstName")
    call = calls[-1]
    assert call["url"] == "https://acme.ilucca.net/api/v3/users/42"
    assert call["params"] == {"fields": "id,firstName"}
    assert result == {"id": 42, "firstName": "Jean"}


def test_get_leave_by_id(c, transport):
    calls, responses = transport
    responses.append(_Resp({"data": {"id": 9, "isAm": True}}))
    result = c.get_leave(9)
    assert calls[-1]["url"] == "https://acme.ilucca.net/api/v3/leaves/9"
    assert result == {"id": 9, "isAm": True}


def test_get_leave_request_by_id_unwraps_data_flat(c, transport):
    # Vérifié sur la doc : `data` référence directement le schéma LeaveRequest
    # (pas de clé intermédiaire "LeaveRequest" dans la réponse).
    calls, responses = transport
    responses.append(_Resp({"data": {"id": 5, "status": 2}}))
    result = c.get_leave_request(5)
    assert calls[-1]["url"] == "https://acme.ilucca.net/api/v3/leaveRequests/5"
    assert result == {"id": 5, "status": 2}


def test_get_department_by_id(c, transport):
    calls, responses = transport
    responses.append(_Resp({"data": {"id": 3, "name": "R&D"}}))
    result = c.get_department(3)
    assert calls[-1]["url"] == "https://acme.ilucca.net/api/v3/departments/3"
    assert result == {"id": 3, "name": "R&D"}


# --- Refus amont : 401 / 404 / 429 ------------------------------------------

def test_401_raises_upstream_error(c, transport):
    _, responses = transport
    responses.append(_Resp({"Status": 401, "Message": "Invalid API key"}, status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc_info:
        c.list_users()
    assert exc_info.value.status_code == 401
    assert exc_info.value.is_client_error


def test_404_raises_upstream_error(c, transport):
    _, responses = transport
    responses.append(_Resp({"Status": 404, "Message": "Not Found"}, status_code=404))
    with pytest.raises(UpstreamHTTPError) as exc_info:
        c.get_user(999999)
    assert exc_info.value.status_code == 404


def test_429_is_retried_then_succeeds(c, transport, monkeypatch):
    monkeypatch.setattr(lucca_client.time, "sleep", lambda *_: None)
    _, responses = transport
    responses.append(_Resp({"Message": "Too Many Requests"}, status_code=429))
    responses.append(_Resp({"data": {"items": [{"id": 1}], "count": 1}}))
    result = c.list_users()
    assert result == [{"id": 1}]
