"""Contrat du client Luma (en-tête `x-luma-api-key`, pagination par curseur).

Mocke `requests.Session.request`. Ce que ce fichier verrouille :

1. la clé ne part qu'en en-tête, et `calendar_id` (clé d'ORGANISATION) part en
   `x-luma-calendar-id` ;
2. chaque méthode appelle le bon verbe et le bon chemin — et la table
   `ROUTES` couvre TOUTES les routes de l'OpenAPI publié par Luma (relevé du
   2026-10-09), sans route inventée ;
3. les filtres multi-valués partent en clés RÉPÉTÉES, pas joints par virgule ;
4. une lecture est retentée (429 court, 5xx), une écriture JAMAIS : l'API n'a
   pas de clé d'idempotence, et rejouer un POST inviterait deux fois ;
5. `iterate` boucle sur `has_more`, pas sur la taille de `entries` ;
6. les refus locaux nomment la valeur attendue.
"""
from __future__ import annotations

import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.luma import client as lc


class _Resp:
    def __init__(self, status_code: int = 200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {"entries": [], "has_more": False}
        self.content = b"x"
        self.text = str(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "replies": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, kwargs=kwargs, headers=dict(self.headers))
        seen["calls"].append((method, url[len(lc.BASE_URL):]))
        if seen["replies"]:
            return seen["replies"].pop(0)
        return _Resp(200)

    sleeps = []
    monkeypatch.setattr(lc.requests.Session, "request", fake_request)
    monkeypatch.setattr(lc.time, "sleep", sleeps.append)
    seen["sleeps"] = sleeps
    return seen


@pytest.fixture()
def cli():
    return lc.LumaClient(api_key="luma_secret")


# --- authentification ------------------------------------------------------

def test_cle_en_entete_jamais_en_query(cli, capture):
    cli.list_events()
    assert capture["headers"]["x-luma-api-key"] == "luma_secret"
    assert "x-luma-calendar-id" not in capture["headers"]
    assert "luma_secret" not in capture["url"]
    assert all("luma_secret" not in str(v) for _k, v in capture["kwargs"]["params"] or [])


def test_cle_d_organisation_vise_un_calendrier(capture):
    c = lc.LumaClient(api_key="k", calendar_id="  cal-123 ")
    c.get_calendar()
    assert capture["headers"]["x-luma-calendar-id"] == "cal-123"


def test_cle_absente_refusee():
    from oto.tools.common.credentials import MissingCredential
    with pytest.raises(MissingCredential):
        lc.LumaClient(api_key=None)


# --- routes ------------------------------------------------------------------

# (méthode du client, args, kwargs, verbe, chemin)
ROUTES = [
    ("get_self", (), {}, "GET", "/v1/users/get-self"),
    ("lookup_entity", ("my-event",), {}, "GET", "/v1/entities/lookup"),
    ("search_places", ("Paris",), {}, "GET", "/v1/places/search"),
    ("search_images", ("dinner",), {}, "GET", "/v1/images/search"),
    ("create_upload_url", (), {"content_type": "image/png"}, "POST",
     "/v1/images/create-upload-url"),
    ("list_organization_admins", (), {}, "GET", "/v1/organizations/admins/list"),
    ("list_organization_calendars", (), {}, "GET",
     "/v1/organizations/calendars/list"),
    ("list_organization_events", (), {}, "GET", "/v1/organizations/events/list"),
    ("create_organization_calendar", ("Club",), {}, "POST",
     "/v2/organizations/calendars/create"),
    ("transfer_event_calendar", ("evt-1", "cal-2"), {}, "POST",
     "/v1/organizations/events/transfer-calendar"),
    ("get_event", ("evt-1",), {}, "GET", "/v1/events/get"),
    ("list_events", (), {}, "GET", "/v1/calendars/events/list"),
    ("lookup_event", (), {"event_id": "evt-1"}, "GET",
     "/v1/calendars/events/lookup"),
    ("create_event", ({"name": "N", "start_at": "2026-11-01T18:00:00Z",
                       "timezone": "Europe/Paris"},), {}, "POST", "/v1/events/create"),
    ("update_event", ("evt-1", {"name": "M"}), {}, "POST", "/v1/events/update"),
    ("request_event_cancellation", ("evt-1",), {}, "POST",
     "/v1/events/cancel/request"),
    ("cancel_event", ("evt-1", "tok"), {}, "POST", "/v1/events/cancel"),
    ("add_event_to_calendar", ({"platform": "luma", "event_id": "evt-1"},), {},
     "POST", "/v1/calendars/events/add"),
    ("approve_event", ("calev-1",), {}, "POST", "/v1/calendars/events/approve"),
    ("reject_event", ("calev-1",), {}, "POST", "/v1/calendars/events/reject"),
    ("add_host", ("evt-1", "h@example.test"), {}, "POST", "/v1/events/hosts/add"),
    ("update_host", ("evt-1", "h@example.test"), {"access_level": "manager"},
     "POST", "/v1/events/hosts/update"),
    ("remove_host", ("evt-1", "h@example.test"), {}, "POST",
     "/v1/events/hosts/remove"),
    ("list_guests", ("evt-1",), {}, "GET", "/v1/events/guests/list"),
    ("get_guest", ("evt-1", "gst-1"), {}, "GET", "/v1/events/guests/get"),
    ("add_guests", ("evt-1", [{"email": "a@example.test"}]), {}, "POST",
     "/v1/events/guests/add"),
    ("update_guest_status", ("evt-1", "gst-1", "approved"), {}, "POST",
     "/v1/events/guests/update-status"),
    ("update_guest_tickets", ("evt-1", "gst-1"),
     {"add_ticket_type_ids": ["ttype-1"]}, "POST",
     "/v1/events/guests/update-tickets"),
    ("send_invites", ("evt-1", [{"email": "a@example.test"}]), {}, "POST",
     "/v1/events/guests/send-invites"),
    ("list_blasts", ("evt-1",), {}, "GET", "/v1/events/blasts/list"),
    ("get_blast", ("ep-1",), {}, "GET", "/v1/events/blasts/get"),
    ("create_blast", ("evt-1", "Hello"), {}, "POST", "/v1/events/blasts/create"),
    ("update_blast", ("ep-1",), {"subject": "S"}, "POST",
     "/v1/events/blasts/update"),
    ("delete_blast", ("ep-1",), {}, "POST", "/v1/events/blasts/delete"),
    ("list_ticket_types", ("evt-1",), {}, "GET", "/v1/events/ticket-types/list"),
    ("get_ticket_type", ("ttype-1",), {}, "GET", "/v1/events/ticket-types/get"),
    ("create_ticket_type", ("evt-1", {"name": "VIP", "type": "free"}), {}, "POST",
     "/v1/events/ticket-types/create"),
    ("update_ticket_type", ("ttype-1", {"name": "VIP+"}), {}, "POST",
     "/v1/events/ticket-types/update"),
    ("delete_ticket_type", ("ttype-1",), {}, "POST",
     "/v1/events/ticket-types/delete"),
    ("list_event_coupons", ("evt-1",), {}, "GET", "/v1/events/coupons/list"),
    ("list_calendar_coupons", (), {}, "GET", "/v1/calendars/coupons/list"),
    ("create_coupon", ("EARLY", {"discount_type": "percent", "percent_off": 10}),
     {"event_id": "evt-1"}, "POST", "/v1/events/coupons/create"),
    ("create_coupon", ("EARLY", {"discount_type": "percent", "percent_off": 10}),
     {}, "POST", "/v1/calendars/coupons/create"),
    ("update_coupon", ("EARLY",), {"event_id": "evt-1", "remaining_count": 5},
     "POST", "/v1/events/coupons/update"),
    ("update_coupon", ("EARLY",), {"remaining_count": 5}, "POST",
     "/v1/calendars/coupons/update"),
    ("get_calendar", (), {}, "GET", "/v1/calendars/get"),
    ("update_calendar", ("cal-1", {"name": "C"}), {}, "POST",
     "/v1/calendars/update"),
    ("list_calendar_admins", (), {}, "GET", "/v1/calendars/admins/list"),
    ("add_calendar_admins", (["a@example.test"],), {}, "POST",
     "/v1/calendars/admins/add"),
    ("list_event_tags", (), {}, "GET", "/v1/calendars/event-tags/list"),
    ("create_event_tag", ("Talks",), {}, "POST", "/v1/calendars/event-tags/create"),
    ("update_event_tag", ("tag-1",), {"name": "T"}, "POST",
     "/v1/calendars/event-tags/update"),
    ("delete_event_tag", ("tag-1",), {}, "POST", "/v1/calendars/event-tags/delete"),
    ("apply_event_tag", ("Talks", ["evt-1"]), {}, "POST",
     "/v1/calendars/event-tags/apply"),
    ("unapply_event_tag", ("Talks", ["evt-1"]), {}, "POST",
     "/v1/calendars/event-tags/unapply"),
    ("list_contacts", (), {}, "GET", "/v1/calendars/contacts/list"),
    ("import_contacts", ([{"email": "a@example.test"}],), {}, "POST",
     "/v1/calendars/contacts/import"),
    ("block_contact", (), {"email": "a@example.test"}, "POST",
     "/v1/calendars/contacts/block"),
    ("remove_contact", (), {"contact_id": "c-1"}, "POST",
     "/v1/calendars/contacts/remove"),
    ("restore_contact", (), {"email": "a@example.test"}, "POST",
     "/v1/calendars/contacts/restore"),
    ("list_contact_tags", (), {}, "GET", "/v1/calendars/contact-tags/list"),
    ("create_contact_tag", ("VIP",), {"color": "red"}, "POST",
     "/v1/calendars/contact-tags/create"),
    ("update_contact_tag", ("tag-1",), {"name": "V"}, "POST",
     "/v1/calendars/contact-tags/update"),
    ("delete_contact_tag", ("tag-1",), {}, "POST",
     "/v1/calendars/contact-tags/delete"),
    ("apply_contact_tag", ("VIP",), {"emails": ["a@example.test"]}, "POST",
     "/v1/calendars/contact-tags/apply"),
    ("unapply_contact_tag", ("VIP",), {"user_ids": ["usr-1"]}, "POST",
     "/v1/calendars/contact-tags/unapply"),
    ("list_membership_tiers", (), {}, "GET", "/v1/memberships/tiers/list"),
    ("add_member", ("a@example.test", "tier-1"), {}, "POST",
     "/v1/memberships/members/add"),
    ("update_member_status", ("usr-1", "approved"), {}, "POST",
     "/v1/memberships/members/update-status"),
    ("list_webhooks", (), {}, "GET", "/v1/webhooks/list"),
    ("get_webhook", ("wh-1",), {}, "GET", "/v2/webhooks/get"),
    ("create_webhook", ("https://example.test/hook", ["*"]), {}, "POST",
     "/v2/webhooks/create"),
    ("update_webhook", ("wh-1",), {"status": "paused"}, "POST",
     "/v2/webhooks/update"),
    ("delete_webhook", ("wh-1",), {}, "POST", "/v1/webhooks/delete"),
]

# Toutes les routes de https://public-api.luma.com/openapi.json au 2026-10-09.
OPENAPI_ROUTES = {
    ("GET", p) for p in (
        "/v1/events/get", "/v1/calendars/get", "/v1/calendars/events/list",
        "/v1/events/guests/get", "/v1/events/guests/list", "/v1/users/get-self",
        "/v1/calendars/contact-tags/list", "/v1/calendars/event-tags/list",
        "/v1/calendars/admins/list", "/v1/entities/lookup", "/v1/places/search",
        "/v1/images/search", "/v1/calendars/events/lookup",
        "/v1/calendars/contacts/list", "/v1/events/coupons/list",
        "/v1/calendars/coupons/list", "/v1/events/ticket-types/list",
        "/v1/events/ticket-types/get", "/v1/events/blasts/list",
        "/v1/events/blasts/get", "/v1/memberships/tiers/list",
        "/v1/webhooks/list", "/v2/webhooks/get", "/v1/organizations/admins/list",
        "/v1/organizations/calendars/list", "/v1/organizations/events/list")
} | {
    ("POST", p) for p in (
        "/v1/events/create", "/v1/events/update",
        "/v1/events/guests/update-status", "/v1/events/guests/update-tickets",
        "/v1/events/guests/send-invites", "/v1/events/guests/add",
        "/v1/events/hosts/add", "/v1/events/hosts/update",
        "/v1/events/hosts/remove", "/v1/events/coupons/create",
        "/v1/events/coupons/update", "/v1/calendars/coupons/create",
        "/v1/calendars/coupons/update", "/v1/calendars/contacts/import",
        "/v1/calendars/contacts/block", "/v1/calendars/contacts/remove",
        "/v1/calendars/contacts/restore", "/v1/calendars/contact-tags/create",
        "/v1/calendars/contact-tags/update", "/v1/calendars/contact-tags/delete",
        "/v1/calendars/contact-tags/apply", "/v1/calendars/contact-tags/unapply",
        "/v1/calendars/event-tags/create", "/v1/calendars/event-tags/update",
        "/v1/calendars/event-tags/delete", "/v1/calendars/event-tags/apply",
        "/v1/calendars/event-tags/unapply", "/v1/calendars/events/add",
        "/v1/calendars/events/approve", "/v1/calendars/events/reject",
        "/v1/images/create-upload-url", "/v1/events/ticket-types/create",
        "/v1/events/ticket-types/update", "/v1/events/ticket-types/delete",
        "/v1/events/blasts/create", "/v1/events/blasts/update",
        "/v1/events/blasts/delete", "/v1/memberships/members/add",
        "/v1/memberships/members/update-status", "/v2/webhooks/create",
        "/v2/webhooks/update", "/v1/webhooks/delete",
        "/v1/events/cancel/request", "/v1/events/cancel",
        "/v1/calendars/admins/add", "/v1/calendars/update",
        "/v2/organizations/calendars/create",
        "/v1/organizations/events/transfer-calendar")
}


@pytest.mark.parametrize("name,args,kwargs,verb,path", ROUTES,
                         ids=[f"{r[0]}:{r[4]}" for r in ROUTES])
def test_route(cli, capture, name, args, kwargs, verb, path):
    getattr(cli, name)(*args, **kwargs)
    assert capture["calls"] == [(verb, path)]


def test_toutes_les_routes_publiees_sont_couvertes_et_aucune_inventee():
    covered = {(r[3], r[4]) for r in ROUTES}
    assert covered == OPENAPI_ROUTES


def test_les_ecritures_partent_en_json_les_lectures_en_query(cli, capture):
    cli.update_guest_status("evt-1", "a@example.test", "declined", send_email=False)
    assert capture["kwargs"]["json"] == {
        "event_id": "evt-1", "guest_id": "a@example.test", "status": "declined",
        "send_email": False}
    assert capture["kwargs"]["params"] is None
    cli.get_guest("evt-1", "gst-1")
    assert capture["kwargs"]["params"] == [("event_id", "evt-1"), ("id", "gst-1")]


def test_un_argument_omis_ne_vide_rien_un_null_explicite_si(cli, capture):
    cli.update_calendar("cal-1", {"website": None})
    assert capture["kwargs"]["json"] == {"calendar_id": "cal-1", "website": None}
    cli.reject_event("calev-1")
    assert capture["kwargs"]["json"] == {"calendar_event_id": "calev-1"}


def test_les_obligatoires_priment_sur_fields(cli, capture):
    cli.update_event("evt-1", {"event_id": "evt-AUTRE", "name": "N"})
    assert capture["kwargs"]["json"]["event_id"] == "evt-1"


# --- paramètres ----------------------------------------------------------------

def test_filtres_multivalues_en_cles_repetees(cli, capture):
    cli.list_events(access=["manage", "view"], platforms=["luma", "external"],
                    limit=20, cursor="c1")
    params = capture["kwargs"]["params"]
    assert ("access", "manage") in params and ("access", "view") in params
    assert ("platforms", "external") in params
    assert ("pagination_limit", 20) in params and ("pagination_cursor", "c1") in params


def test_booleens_en_minuscules(cli, capture):
    cli.list_ticket_types("evt-1", include_hidden=True)
    assert ("include_hidden", "true") in capture["kwargs"]["params"]


def test_tickets_ajoutes_sous_la_forme_attendue(cli, capture):
    cli.add_guests("evt-1", [{"email": "a@example.test"}],
                   ticket_type_ids=["ttype-1", "ttype-2"])
    assert capture["kwargs"]["json"]["tickets"] == [
        {"event_ticket_type_id": "ttype-1"}, {"event_ticket_type_id": "ttype-2"}]


# --- refus locaux --------------------------------------------------------------

@pytest.mark.parametrize("call,needle", [
    (lambda c: c.list_guests("evt-1", approval_status="going"), "approval_status"),
    (lambda c: c.update_guest_status("evt-1", "g", "invited"), "status"),
    (lambda c: c.list_events(access=["edit"]), "access"),
    (lambda c: c.create_event({"name": "N"}), "fields.start_at"),
    (lambda c: c.add_guests("evt-1", [{"name": "no mail"}]), "email"),
    (lambda c: c.create_coupon("X", {"discount_type": "amount", "cents_off": 5}),
     "currency"),
    (lambda c: c.create_coupon("X", {"discount_type": "percent", "percent_off": 5},
                               ticket_type_id="ttype-1"), "event_id"),
    (lambda c: c.create_blast("evt-1", "x", recipient_groups=[{"status": "going"}]),
     "recipient_groups[0].status"),
    (lambda c: c.block_contact(), "contact_id"),
    (lambda c: c.create_webhook("https://x.test", ["guest.deleted"]), "event_types"),
    (lambda c: c.update_guest_tickets("evt-1", "g"), "add_ticket_type_ids"),
    (lambda c: c.list_events(limit=0), "limit"),
    (lambda c: c.lookup_event(), "event_id"),
])
def test_refus_locaux(cli, capture, call, needle):
    with pytest.raises(ValueError) as exc:
        call(cli)
    assert needle in str(exc.value)
    assert capture["calls"] == []


# --- re-tentatives ---------------------------------------------------------------

def test_lecture_retentee_sur_429_court(cli, capture):
    capture["replies"] = [_Resp(429, {"message": "slow"}, {"Retry-After": "2"}),
                          _Resp(200, {"entries": []})]
    cli.list_events()
    assert len(capture["calls"]) == 2 and capture["sleeps"] == [2.0]


def test_lecture_non_retentee_sur_429_long(cli, capture):
    capture["replies"] = [_Resp(429, {"message": "slow"}, {"Retry-After": "60"})]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.list_events()
    assert exc.value.status_code == 429 and len(capture["calls"]) == 1


def test_lecture_retentee_sur_5xx(cli, capture):
    capture["replies"] = [_Resp(502, "bad"), _Resp(503, "bad"), _Resp(200, {})]
    cli.get_calendar()
    assert len(capture["calls"]) == 3


def test_ecriture_jamais_rejouee(cli, capture):
    capture["replies"] = [_Resp(503, "down")]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.send_invites("evt-1", [{"email": "a@example.test"}])
    assert exc.value.status_code == 503 and len(capture["calls"]) == 1
    assert exc.value.service == "luma"


# --- pagination ------------------------------------------------------------------

def test_iterate_suit_has_more_pas_la_taille(cli, capture):
    capture["replies"] = [
        _Resp(200, {"entries": [], "has_more": True, "next_cursor": "c2"}),
        _Resp(200, {"entries": [{"id": 1}], "has_more": True, "next_cursor": "c3"}),
        _Resp(200, {"entries": [{"id": 2}], "has_more": False}),
    ]
    rows = list(cli.iterate(cli.list_guests, "evt-1"))
    assert rows == [{"id": 1}, {"id": 2}]
    assert len(capture["calls"]) == 3


def test_iterate_refuse_un_curseur(cli):
    with pytest.raises(ValueError):
        list(cli.iterate(cli.list_events, cursor="x"))
