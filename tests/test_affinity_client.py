"""Contrat du client Affinity (API v1 + v2, une clé Bearer).

Banc tenu CONTRE LA DOCUMENTATION publiée par Affinity :

- v2 : chaque requête que le client construit est confrontée à un extrait de la
  spec OpenAPI publiée par Affinity (`fixtures/affinity_v2_openapi_subset.json`,
  tiré de developer.affinity.co/api-reference/openapi.json le 02/10/2026, version
  d'API 2026-09-17) : chemin et méthode déclarés, paramètres de requête connus,
  corps validé par JSON Schema.
- v1 (pas de spec machine) : méthode, chemin et clés relevés à la main dans
  api-docs.affinity.co.
- transport : en-tête Bearer sur les deux générations, version épinglée sur v2
  SEULEMENT, re-tentative unique d'un GET en 429, corps d'erreur texte (v1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from oto.tools.affinity import client as af
from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError


SPEC = json.loads(
    (Path(__file__).parent / "fixtures" / "affinity_v2_openapi_subset.json").read_text())


class _Resp:
    def __init__(self, status_code=200, body=None, headers=None, text=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)
        self.content = self.text.encode()
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"calls": [], "responses": [], "sleeps": []}

    def fake_request(self, method, url, **kwargs):
        seen["calls"].append({"method": method, "url": url, "params": kwargs.get("params"),
                              "json": kwargs.get("json"), "headers": kwargs.get("headers"),
                              "session_headers": dict(self.headers)})
        if seen["responses"]:
            return seen["responses"].pop(0)
        return _Resp(200, {"data": []})

    monkeypatch.setattr(af.requests.Session, "request", fake_request)
    monkeypatch.setattr(af.time, "sleep", lambda s: seen["sleeps"].append(s))
    return seen


@pytest.fixture()
def cli():
    return af.AffinityClient(api_key="KEY")


def _last(capture):
    return capture["calls"][-1]


# --- spec helpers ------------------------------------------------------------

def _template_regex(template: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", template) + "$")


def _spec_operation(method: str, path: str):
    for template, ops in SPEC["paths"].items():
        if _template_regex(template).match(path):
            op = ops.get(method.lower())
            assert op is not None, f"{method} {template} is not in Affinity's spec"
            return template, op, ops.get("parameters", [])
    raise AssertionError(f"{path} matches no path of Affinity's v2 spec")


def _resolve(node):
    if isinstance(node, dict) and "$ref" in node:
        _, section, kind, name = node["$ref"].split("/")
        return SPEC[section][kind][name]
    return node


def _validator(schema):
    root = dict(schema)
    root["components"] = SPEC["components"]
    return jsonschema.Draft202012Validator(root)


def assert_v2_call_matches_spec(call):
    assert call["url"].startswith(af.BASE_URL + "/v2/")
    path = call["url"][len(af.BASE_URL):]
    template, op, shared = _spec_operation(call["method"], path)
    params = [_resolve(p) for p in op.get("parameters", []) + shared]
    declared = {p["name"] for p in params if p.get("in") == "query"}
    sent = set((call["params"] or {}).keys())
    assert sent <= declared, f"{template}: undeclared query params {sent - declared}"
    for p in params:
        if p.get("in") == "query" and p["name"] in sent:
            value = call["params"][p["name"]]
            schema = p.get("schema", {})
            values = value if isinstance(value, list) else [value]
            item_schema = schema.get("items", schema) if schema.get("type") == "array" else schema
            for v in values:
                if item_schema.get("type") == "integer":
                    v = int(v)
                _validator(item_schema).validate(v)
    body = op.get("requestBody")
    if call["json"] is not None:
        assert body, f"{template} takes no body"
        schema = _resolve(body)["content"]["application/json"]["schema"]
        _validator(schema).validate(call["json"])
    else:
        assert not (body or {}).get("required"), f"{template} requires a body"
    return template


# --- authentication & transport ------------------------------------------------

def test_la_cle_est_exigee(monkeypatch):
    monkeypatch.delenv("AFFINITY_API_KEY", raising=False)
    with pytest.raises(MissingCredential):
        af.AffinityClient(api_key=None)


def test_bearer_sur_v1_et_v2_version_sur_v2_seulement(capture, cli):
    cli.whoami()
    v2 = _last(capture)
    cli.search_persons("doe")
    v1 = _last(capture)
    for call in (v1, v2):
        assert call["session_headers"]["Authorization"] == "Bearer KEY"
        assert "KEY" not in call["url"]
    assert v2["headers"] == {"X-Affinity-Api-Version": "2026-09-17"}
    assert v1["headers"] is None
    assert v1["url"] == "https://api.affinity.co/persons"


def test_la_version_epinglee_est_celle_de_la_spec_vendue():
    assert af.API_VERSION == SPEC["info"]["x-affinity-api-version"]


def test_base_url_surchargeable_pour_un_serveur_de_maquette(capture):
    af.AffinityClient(api_key="K", base_url="http://127.0.0.1:4010/").whoami()
    assert _last(capture)["url"] == "http://127.0.0.1:4010/v2/auth/whoami"


def test_get_429_retente_une_fois_si_le_compteur_repart_vite(capture, cli):
    capture["responses"] += [
        _Resp(429, {"errors": []}, {"x-ratelimit-limit-user-reset": "3"}),
        _Resp(200, {"data": []}, {"x-ratelimit-limit-org-remaining": "99000"}),
    ]
    cli.list_lists()
    assert capture["sleeps"] == [3.0]
    assert len(capture["calls"]) == 2
    assert cli.last_rate_limit == {"org_remaining": 99000}


def test_une_ecriture_en_429_n_est_jamais_rejouee(capture, cli):
    capture["responses"] += [_Resp(429, {"errors": []}, {"x-ratelimit-limit-user-reset": "1"})]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.create_organization("Acme")
    assert exc.value.status_code == 429
    assert len(capture["calls"]) == 1 and capture["sleeps"] == []


def test_erreur_v1_en_texte_brut_garde_son_corps(capture, cli):
    capture["responses"] += [_Resp(401, "Unauthorized API Key.", text="Unauthorized API Key.")]
    with pytest.raises(UpstreamHTTPError) as exc:
        cli.search_organizations("acme")
    assert exc.value.status_code == 401
    assert "Unauthorized API Key." in str(exc.value.body)


def test_204_rend_un_dict_vide(capture, cli):
    capture["responses"] += [_Resp(204, text="")]
    assert cli.update_note(5, content="hello") == {}


# --- v2 requests against Affinity's published spec -----------------------------

LOC = {"city": "Paris", "country": "France"}
V2_CALLS = {
    "whoami": lambda c: c.whoami(),
    "rate_limit": lambda c: c.rate_limit(),
    "get_person": lambda c: c.get_person(7, field_types=["global", "enriched"]),
    "get_person_ids": lambda c: c.get_person(7, field_ids=["field-1", "affinity-data-location"]),
    "get_company": lambda c: c.get_company("8", field_types=["relationship-intelligence"]),
    "get_opportunity": lambda c: c.get_opportunity(9),
    "person_entries": lambda c: c.entity_list_entries("person", 7, limit=50),
    "company_entries": lambda c: c.entity_list_entries("company", 8, cursor="abc"),
    "person_relationships": lambda c: c.relationships("person", 7, limit=10),
    "company_relationships": lambda c: c.relationships("company", 8),
    "person_fields": lambda c: c.global_fields("person"),
    "company_fields": lambda c: c.global_fields("company", limit=100),
    "person_patch": lambda c: c.update_entity_fields("person", 7, [
        {"id": "field-1", "value": af.field_value("text", "VC")},
        {"id": "field-2", "value": af.field_value("dropdown", 33)}]),
    "company_patch": lambda c: c.update_entity_fields("company", 8, [
        {"id": "field-3", "value": af.field_value("location", LOC)},
        {"id": "field-4", "value": af.field_value("number", None)}]),
    "lists": lambda c: c.list_lists(term="Deals", limit=20),
    "list": lambda c: c.get_list(1),
    "list_fields": lambda c: c.list_fields(1),
    "views": lambda c: c.list_saved_views(1),
    "entries": lambda c: c.list_entries(1, field_types=["list", "global"], limit=100),
    "entries_ids": lambda c: c.list_entries(1, field_ids=["field-9"], cursor="x"),
    "view_entries": lambda c: c.saved_view_entries(1, 2, limit=5),
    "entry": lambda c: c.get_list_entry(1, 3, field_types=["list"]),
    "entry_patch": lambda c: c.update_list_entry_fields(1, 3, [
        {"id": "field-5", "value": af.field_value("dropdown-multi", [1, {"dropdownOptionId": 2}])},
        {"id": "field-6", "value": af.field_value("datetime", "2026-10-02")},
        {"id": "field-7", "value": af.field_value("person-multi", [4, 5])},
        {"id": "field-8", "value": af.field_value("ranked-dropdown", 6)}]),
    "list_dropdown": lambda c: c.dropdown_options("field-5", list_id=1),
    "person_dropdown": lambda c: c.dropdown_options("field-2", kind="person"),
    "company_dropdown": lambda c: c.dropdown_options("field-2", kind="company", limit=100),
    "notes": lambda c: c.list_notes(filter="createdAt>2026-01-01T00:00:00Z", limit=20),
    "person_notes": lambda c: c.list_notes(kind="person", entity_id=7),
    "company_notes": lambda c: c.list_notes(kind="company", entity_id=8),
    "opportunity_notes": lambda c: c.list_notes(kind="opportunity", entity_id=9),
    "note": lambda c: c.get_note(11),
    "create_note": lambda c: c.create_note("Call recap\n\nNext: send deck", person_ids=[7],
                                           company_ids=[8]),
    "update_note": lambda c: c.update_note(11, content="<p>Edited</p>", opportunity_ids=[]),
    "delete_note": lambda c: c.delete_note(11),
}


@pytest.mark.parametrize("name", sorted(V2_CALLS))
def test_chaque_requete_v2_respecte_la_spec(name, capture, cli):
    V2_CALLS[name](cli)
    assert_v2_call_matches_spec(_last(capture))


def test_les_champs_demandes_partent_en_parametres_repetes(capture, cli):
    cli.list_entries(1, field_types=["list", "global"])
    assert _last(capture)["params"] == {"fieldTypes": ["list", "global"]}


def test_un_type_de_champ_inconnu_est_refuse_localement(capture, cli):
    with pytest.raises(ValueError, match="field_types"):
        cli.list_entries(1, field_types=["custom"])
    assert capture["calls"] == []


# --- field values: the write shapes cover what Affinity accepts ------------------

def _update_types():
    out = set()
    for variant in SPEC["components"]["schemas"]["FieldValueUpdate"]["oneOf"]:
        prop = _resolve(variant)["properties"]["type"]
        out |= {prop["const"]} if "const" in prop else set(prop["enum"])
    return out


def test_les_types_ecrivables_sont_exactement_ceux_de_la_spec():
    assert af.WRITABLE_VALUE_TYPES == _update_types()


SAMPLES = {
    "text": "hello", "filterable-text": "seed", "filterable-text-multi": ["a", "b"],
    "number": "12.5", "number-multi": [1, 2.5], "datetime": "2026-10-02T09:00:00Z",
    "dropdown": 3, "ranked-dropdown": {"dropdownOptionId": 4}, "dropdown-multi": [5, 6],
    "person": 7, "person-multi": [{"id": 8}], "company": "9", "company-multi": [10],
    "location": LOC, "location-multi": [LOC, {"city": "Lyon"}],
}


@pytest.mark.parametrize("value_type", sorted(SAMPLES))
def test_field_value_valide_contre_la_spec(value_type):
    schema = {"$ref": "#/components/schemas/FieldValueUpdate"}
    _validator(schema).validate(af.field_value(value_type, SAMPLES[value_type]))
    _validator(schema).validate(af.field_value(value_type, None))  # clear


def test_samples_couvrent_tous_les_types():
    assert set(SAMPLES) == set(af.WRITABLE_VALUE_TYPES)


@pytest.mark.parametrize("value_type", ["formula-number", "interaction", "note", "list-multi",
                                        "reminder"])
def test_les_champs_calcules_sont_refuses(value_type):
    with pytest.raises(ValueError, match="computed by Affinity"):
        af.field_value(value_type, 1)


def test_un_texte_n_est_jamais_pris_pour_une_option():
    with pytest.raises(ValueError, match="dropdownOptionId"):
        af.field_value("dropdown", "Won")


def test_plus_de_100_mises_a_jour_refusees(capture, cli):
    updates = [{"id": f"field-{i}", "value": af.field_value("text", "x")} for i in range(101)]
    with pytest.raises(ValueError, match="At most 100"):
        cli.update_list_entry_fields(1, 2, updates)
    assert capture["calls"] == []


# --- v1 requests against api-docs.affinity.co -----------------------------------
# (method, path, allowed query keys, allowed body keys, required body keys)

V1_CALLS = {
    "search_persons": (lambda c: c.search_persons("doe", with_interaction_dates=True,
                                                  page_size=10, page_token="t"),
                       "GET", "/persons",
                       {"term", "with_interaction_dates", "page_size", "page_token"}, None, None),
    "search_organizations": (lambda c: c.search_organizations("acme.co"),
                             "GET", "/organizations", {"term"}, None, None),
    "create_person": (lambda c: c.create_person("Ada", "Lovelace", ["ada@x.io"], [3]),
                      "POST", "/persons", set(),
                      {"first_name", "last_name", "emails", "organization_ids"},
                      {"first_name", "last_name", "emails"}),
    "update_person": (lambda c: c.update_person(7, emails=["a@x.io", "b@x.io"]),
                      "PUT", "/persons/7", set(),
                      {"first_name", "last_name", "emails", "organization_ids"}, set()),
    "create_organization": (lambda c: c.create_organization("Acme", "acme.co", [7]),
                            "POST", "/organizations", set(), {"name", "domain", "person_ids"},
                            {"name"}),
    "update_organization": (lambda c: c.update_organization(8, name="Acme Inc"),
                            "PUT", "/organizations/8", set(), {"name", "domain", "person_ids"},
                            set()),
    "create_opportunity": (lambda c: c.create_opportunity("Series A", 4, [7], [8]),
                           "POST", "/opportunities", set(),
                           {"name", "list_id", "person_ids", "organization_ids"},
                           {"name", "list_id"}),
    "update_opportunity": (lambda c: c.update_opportunity(9, name="Series B"),
                           "PUT", "/opportunities/9", set(),
                           {"name", "person_ids", "organization_ids"}, set()),
    "add_list_entry": (lambda c: c.add_list_entry(1, 8, creator_id=2),
                       "POST", "/lists/1/list-entries", set(), {"entity_id", "creator_id"},
                       {"entity_id"}),
    "remove_list_entry": (lambda c: c.remove_list_entry(1, 3),
                          "DELETE", "/lists/1/list-entries/3", set(), None, None),
    "whoami_v1": (lambda c: c.whoami_v1(), "GET", "/auth/whoami", set(), None, None),
    "get_person_v1": (lambda c: c.get_entity_v1("person", 7), "GET", "/persons/7", set(),
                      None, None),
    "get_organization_v1": (lambda c: c.get_entity_v1("company", 8), "GET",
                            "/organizations/8", set(), None, None),
    "get_opportunity_v1": (lambda c: c.get_entity_v1("opportunity", 9), "GET",
                           "/opportunities/9", set(), None, None),
    "interactions": (lambda c: c.list_interactions("email", organization_id=8,
                                                   start_time="2026-01-01",
                                                   end_time="2026-06-30", direction="sent"),
                     "GET", "/interactions",
                     {"type", "person_id", "organization_id", "opportunity_id", "start_time",
                      "end_time", "direction", "page_size", "page_token", "logging_type",
                      "internal_person_id"}, None, None),
}


@pytest.mark.parametrize("name", sorted(V1_CALLS))
def test_chaque_requete_v1_respecte_la_doc(name, capture, cli):
    fn, method, path, query_keys, body_keys, required = V1_CALLS[name]
    fn(cli)
    call = _last(capture)
    assert call["method"] == method
    assert call["url"] == af.BASE_URL + path
    assert call["headers"] is None  # v1 is unversioned
    assert set((call["params"] or {}).keys()) <= query_keys
    if body_keys is None:
        assert call["json"] is None
    else:
        assert set(call["json"]) <= body_keys
        assert required <= set(call["json"])


def test_interactions_codes_v1(capture, cli):
    cli.list_interactions("meeting", person_id=7, start_time="2026-01-01",
                          end_time="2026-02-01T00:00:00Z")
    assert _last(capture)["params"] == {
        "type": 0, "person_id": 7, "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-02-01T00:00:00Z"}


def test_interactions_exigent_une_seule_ancre_et_une_fenetre(capture, cli):
    with pytest.raises(ValueError, match="exactly one"):
        cli.list_interactions("email", person_id=1, organization_id=2,
                              start_time="2026-01-01", end_time="2026-02-01")
    with pytest.raises(ValueError, match="required"):
        cli.list_interactions("email", person_id=1, start_time=None, end_time="2026-02-01")
    assert capture["calls"] == []


def test_une_opportunite_ne_passe_pas_par_les_list_entries(cli):
    with pytest.raises(ValueError):
        cli.entity_list_entries("opportunity", 9)


# --- notes ---------------------------------------------------------------------

def test_texte_brut_devient_du_html_echappe():
    assert af.note_html("a < b\nnext\n\npara 2") == "<p>a &lt; b<br>next</p><p>para 2</p>"


def test_html_autorise_passe_tel_quel():
    body = '<p>See <a href="https://x.io">deck</a></p><ul><li><strong>Go</strong></li></ul>'
    assert af.note_html(body) == body


@pytest.mark.parametrize("body", ["<p style='x'>a</p>", "<img src='x'>",
                                  "<script>x</script>", '<a href="javascript:x">a</a>'])
def test_html_hors_liste_refuse_localement(body):
    with pytest.raises(ValueError):
        af.note_html(body)


def test_une_note_sans_entite_est_refusee(capture, cli):
    with pytest.raises(ValueError, match="at least one"):
        cli.create_note("hello")
    assert capture["calls"] == []


# --- pagination -------------------------------------------------------------------

def test_next_cursor_lit_next_url():
    page = {"data": [], "pagination": {
        "prevUrl": None, "nextUrl": "https://api.affinity.co/v2/lists?cursor=ICAgIGFm&limit=5"}}
    assert af.next_cursor(page) == "ICAgIGFm"
    assert af.next_cursor({"data": [], "pagination": {"nextUrl": None}}) is None


def test_limit_borne_a_100(cli):
    with pytest.raises(ValueError, match="limit"):
        cli.list_lists(limit=101)
