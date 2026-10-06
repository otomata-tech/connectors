"""Client de l'API admin HelloStock, éprouvé contre un faux serveur qui rejoue le
contrat (`tests/hellostock_fake.py`) — vrai HTTP, vrai transport `requests`.

Ce que ce banc verrouille : l'authentification (401 jeton inconnu, 403 compte non
administrateur, jeton jamais dans l'URL), les noms de paramètres envoyés (le contrat
parle camelCase, le client snake_case), la pagination par curseur jusqu'au bout, les
refus de filtre relayés tels quels, la forme des trois écritures, la re-tentative
réservée aux lectures, et les deux réponses qui ne sont pas l'API (redirection, corps
non JSON).
"""
from __future__ import annotations

import json

import pytest

from hellostock_fake import ADMIN_TOKEN, MEMBER_TOKEN, FakeHelloStock
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.hellostock import client as hs


@pytest.fixture()
def fake():
    srv = FakeHelloStock().start()
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hs.time, "sleep", lambda _s: None)


def _client(fake, token=ADMIN_TOKEN):
    return hs.HelloStockAdminClient(token=token, base_url=fake.base_url)


def _last(fake):
    return fake.requests_log[-1]


# --- authentification ---------------------------------------------------------

def test_the_token_travels_as_a_bearer_header_never_in_the_url(fake):
    _client(fake).list_demandes(limit=1)
    req = _last(fake)
    assert req["headers"]["Authorization"] == f"Bearer {ADMIN_TOKEN}"
    assert ADMIN_TOKEN not in req["path"] and ADMIN_TOKEN not in json.dumps(req["query"])


def test_an_unknown_token_is_a_401_carrying_the_server_message(fake):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake, token="hs_revoque").list_demandes()
    assert e.value.status_code == 401
    assert e.value.body == {"error": "Unauthorized"}


def test_a_non_admin_token_is_a_403_not_a_401(fake):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake, token=MEMBER_TOKEN).get_user(1)
    assert e.value.status_code == 403
    assert e.value.body == {"error": "Forbidden"}


# --- lectures : les noms de paramètres du contrat ---------------------------

def test_list_demandes_sends_the_contract_names_and_drops_none(fake):
    _client(fake).list_demandes(status="published", since="2026-01-01",
                                matiere="inox", limit=5)
    q = _last(fake)["query"]
    assert q == {"status": ["published"], "since": ["2026-01-01"],
                 "matiere": ["inox"], "limit": ["5"]}
    assert _last(fake)["path"] == "/api/admin/demandes"


def test_list_users_maps_booleans_to_camel_case_true_false(fake):
    _client(fake).list_users(is_admin=False, has_offres=True, has_demandes=False,
                             sector="fonderie")
    q = _last(fake)["query"]
    assert q == {"isAdmin": ["false"], "hasOffres": ["true"],
                 "hasDemandes": ["false"], "sector": ["fonderie"]}


def test_list_positionnements_maps_ids_to_camel_case(fake):
    page = _client(fake).list_positionnements(demande_id=2, user_id=3)
    q = _last(fake)["query"]
    assert q == {"demandeId": ["2"], "userId": ["3"]}
    assert set(page) == {"items", "nextCursor", "total"}


def test_list_offres_carries_the_enrichment_queue_filter(fake):
    page = _client(fake).list_offres(certificat="sans-mots-cles")
    assert _last(fake)["query"] == {"certificat": ["sans-mots-cles"]}
    assert page["items"] and all(not o["keywords"] for o in page["items"])


# --- pagination ---------------------------------------------------------------

def test_the_cursor_walks_every_demande_once_newest_first(fake):
    c, seen, cursor, totals = _client(fake), [], None, set()
    while True:
        page = c.list_demandes(limit=50, cursor=cursor)
        totals.add(page["total"])
        seen += [d["id"] for d in page["items"]]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert totals == {120}
    assert seen == sorted(seen, reverse=True) and len(seen) == len(set(seen)) == 120


def test_members_page_by_increasing_id(fake):
    page = _client(fake).list_users(limit=10)
    ids = [u["id"] for u in page["items"]]
    assert ids == sorted(ids) and page["nextCursor"] == str(ids[-1])


# --- les refus de filtre sont relayés, jamais avalés -------------------------

@pytest.mark.parametrize("kwargs", [
    {"status": "archived"}, {"limit": 500}, {"limit": 0}, {"since": "hier"},
    {"departement": "99"}, {"service": "inconnu"}, {"matiere": "or"},
])
def test_a_filter_out_of_the_referential_is_a_400_not_an_empty_list(fake, kwargs):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake).list_demandes(**kwargs)
    assert e.value.status_code == 400 and "error" in e.value.body


def test_an_unknown_record_is_a_404(fake):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake).get_offre(9999)
    assert e.value.status_code == 404


@pytest.mark.parametrize("bad", ["1", "1/../users", True, 0, -3, 1.0, None])
def test_a_path_id_must_be_a_positive_int_and_nothing_leaves(fake, bad):
    with pytest.raises(ValueError):
        _client(fake).get_demande(bad)
    assert fake.requests_log == []


# --- écritures ----------------------------------------------------------------

def test_update_demande_status_patches_only_the_status(fake):
    assert _client(fake).update_demande_status(3, "closed") == {"success": True}
    req = _last(fake)
    assert (req["method"], req["path"]) == ("PATCH", "/api/admin/demandes/3")
    assert json.loads(req["body"]) == {"status": "closed"}


def test_update_offre_sends_only_the_fields_given(fake):
    _client(fake).update_offre(4, keywords=["304L", "1.4307"])
    assert json.loads(_last(fake)["body"]) == {"keywords": ["304L", "1.4307"]}
    _client(fake).update_offre(4, status="published")
    assert json.loads(_last(fake)["body"]) == {"status": "published"}


def test_update_offre_with_nothing_to_write_is_refused_before_the_network(fake):
    with pytest.raises(ValueError):
        _client(fake).update_offre(4)
    assert fake.requests_log == []


def test_keywords_carrying_an_identity_are_refused_by_the_server(fake):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake).update_offre(4, keywords=["304L", "coulée 12345"])
    assert e.value.status_code == 400
    assert fake.db["offres"][4]["keywords"] == ["316l"]


def test_send_demande_posts_user_ids_and_message(fake):
    out = _client(fake).send_demande(2, [5, 6], message="Bonjour")
    req = _last(fake)
    assert (req["method"], req["path"]) == ("POST", "/api/admin/demandes/2/envoyer")
    assert json.loads(req["body"]) == {"userIds": [5, 6], "message": "Bonjour"}
    assert out == {"success": True, "envoyes": 2, "echecs": [], "noop": False}


def test_send_demande_without_message_does_not_send_a_null(fake):
    _client(fake).send_demande(2, [5])
    assert json.loads(_last(fake)["body"]) == {"userIds": [5]}


@pytest.mark.parametrize("ids", [[], None, ["5"], [True]])
def test_send_demande_refuses_recipients_that_are_not_member_ids(fake, ids):
    with pytest.raises(ValueError):
        _client(fake).send_demande(2, ids)
    assert fake.requests_log == []


def test_the_server_bounds_the_recipient_list(fake):
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake).send_demande(2, list(range(1, 52)))
    assert e.value.status_code == 400
    assert fake.db["envois"] == []


# --- re-tentatives : les lectures seules --------------------------------------

def test_a_read_is_retried_on_a_transient_503(fake):
    calls = []
    orig = fake.dispatch

    def flaky(req):
        calls.append(req["path"])
        if len(calls) == 1:
            return 503, {"error": "indisponible"}, {}
        return orig(req)

    fake.dispatch = flaky
    assert _client(fake).list_users(limit=1)["items"]
    assert len(calls) == 2


def test_a_send_is_never_retried_even_on_a_502(fake):
    fake.force["/api/admin/demandes/2/envoyer"] = (
        502, {"error": "Aucun courriel n'a pu partir"}, {})
    with pytest.raises(UpstreamHTTPError) as e:
        _client(fake).send_demande(2, [5])
    assert e.value.status_code == 502
    assert len(fake.requests_log) == 1


# --- ce qui n'est pas l'API ---------------------------------------------------

def test_a_redirect_is_not_followed_and_says_so(fake):
    fake.force["/api/admin/demandes"] = (
        307, b"", {"Location": "https://www.example.test/api/admin/demandes"})
    with pytest.raises(hs.HelloStockProtocolError, match="redirect"):
        _client(fake).list_demandes()
    assert len(fake.requests_log) == 1


def test_a_non_json_success_is_a_protocol_error(fake):
    fake.force["/api/admin/users"] = (200, "<html>connexion</html>",
                                      {"Content-Type": "text/html"})
    with pytest.raises(hs.HelloStockProtocolError, match="without a JSON body"):
        _client(fake).list_users()


def test_the_published_referentials_are_the_fake_servers(fake):
    import hellostock_fake as f
    assert (hs.STATUSES, hs.MATIERES, hs.CERTIFICATS, hs.SECTORS) == \
        (f.STATUSES, f.MATIERES, f.CERTIFICATS, f.SECTORS)
