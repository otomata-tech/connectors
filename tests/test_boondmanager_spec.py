"""Le client BoondManager parle l'API telle que la RÉFÉRENCE la décrit.

Aucune instance Boond n'est joignable depuis les tests : la vérité, ce sont les
fichiers de la référence éditeur copiés dans `fixtures/boondmanager/` — les
schémas de corps de création (`<entity>_bodyPost.json`) et les paramètres de
recherche (`<entity>_search.raml`). Ce fichier tient trois cliquets :

1. les tables de `_spec` (attributs, relations, champs requis, tri, période,
   filtres, pagination) DISENT ce que disent ces fichiers, champ par champ ;
2. tout corps que le client construit est VALIDE contre le schéma officiel ;
3. ce que le client refuse localement, le schéma officiel le refuse aussi — et
   réciproquement sur un jeu de cas : le refus local n'est ni plus large ni plus
   étroit que l'API.
"""
from __future__ import annotations

import json
import pathlib
import re

import jsonschema
import pytest

from oto.tools.boondmanager import _spec
from oto.tools.boondmanager.client import RECORD_TYPES, build_create_body

_FIX = pathlib.Path(__file__).parent / "fixtures" / "boondmanager"
ENTITIES = _spec.ENTITIES
# Laissé hors du connecteur exprès (structure imbriquée créer-ou-lier).
_HORS_SURFACE = {"companies": {"billingDetails"}}


def _schema(entity: str) -> dict:
    return json.loads((_FIX / f"{entity}_bodyPost.json").read_text())


def _data(entity: str) -> dict:
    return _schema(entity)["properties"]["data"]


def _search_raml(entity: str) -> str:
    return (_FIX / f"{entity}_search.raml").read_text()


def _raml_params(entity: str) -> dict:
    """{nom: bloc} des paramètres de requête du GET de recherche."""
    blocs = re.split(r"\n    (?=\w+:\n)", _search_raml(entity))
    return {b.split(":")[0].strip(): b for b in blocs[1:]}


def _enum_puces(bloc: str) -> list:
    return [v for v in re.findall(r"\* `([^`]*)`", bloc) if not v.startswith("<")]


def _validateur(entity: str):
    # Les schémas déclarent `http://json-schema.org/schema#` (sans brouillon) et
    # n'emploient que des mots du draft 4 : on le nomme plutôt que le deviner.
    return jsonschema.Draft4Validator(_schema(entity))


def _valide(entity: str, body: dict) -> bool:
    return _validateur(entity).is_valid(body)


# --- 1. les tables disent ce que dit la référence ---------------------------------

@pytest.mark.parametrize("entity", ENTITIES)
def test_attributs_et_contraintes_de_la_reference(entity):
    officiels = _data(entity)["properties"]["attributes"]["properties"]
    nos = _spec.ATTRIBUTES[entity]
    assert set(nos) == set(officiels) - _HORS_SURFACE.get(entity, set())
    for nom, regle in nos.items():
        off = officiels[nom]
        types = ({off["type"]} if "type" in off
                 else {alt["type"] for alt in off["oneOf"]})
        assert types == {regle["type"]}, nom
        for cle in ("maxLength", "pattern", "minimum"):
            assert regle.get(cle) == off.get(cle), (nom, cle)
        if "items" in regle:
            assert off["items"]["type"] == regle["items"], nom


@pytest.mark.parametrize("entity", ENTITIES)
def test_champs_requis_de_la_reference(entity):
    data = _data(entity)
    attrs = data["properties"]["attributes"]
    rels = data["properties"].get("relationships", {})
    assert tuple(attrs.get("required", ())) == _spec.REQUIRED_ATTRIBUTES[entity]
    assert tuple(rels.get("required", ())) == _spec.REQUIRED_RELATIONSHIPS[entity]


def _types_de_relation(schema: dict) -> set:
    vus = set()

    def walk(x):
        if isinstance(x, dict):
            props = x.get("properties", {})
            if "id" in props and "enum" in props.get("type", {}):
                vus.update(props["type"]["enum"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(schema)
    return vus


@pytest.mark.parametrize("entity", ENTITIES)
def test_relations_de_la_reference(entity):
    officielles = _data(entity)["properties"]["relationships"]["properties"]
    nos = _spec.RELATIONSHIPS[entity]
    assert set(nos) == set(officielles)
    for nom, (types, liste, nullable) in nos.items():
        brut = json.dumps(officielles[nom])
        assert set(types) == _types_de_relation(officielles[nom]), nom
        assert liste == ('"type": "array"' in brut), nom
        assert nullable == ('"type": "null"' in brut), nom


@pytest.mark.parametrize("entity", ENTITIES)
def test_type_json_api_de_la_reference(entity):
    assert _data(entity)["properties"]["type"]["enum"] == [RECORD_TYPES[entity]]


@pytest.mark.parametrize("entity", ENTITIES)
def test_tri_de_la_reference(entity):
    liste = re.search(r'sortList: "([^"]+)"', _search_raml(entity)).group(1)
    assert _spec.SORTS[entity] == tuple(s.strip() for s in liste.split("|"))


@pytest.mark.parametrize("entity", ENTITIES)
def test_periodes_de_la_reference(entity):
    assert _spec.PERIODS[entity] == tuple(_enum_puces(_raml_params(entity)["period"]))


@pytest.mark.parametrize("entity", ENTITIES)
def test_keywords_type_de_la_reference(entity):
    bloc = _raml_params(entity).get("keywordsType")
    officiels = tuple(v for v in _enum_puces(bloc) if v) if bloc else ()
    # `relatedActions` (actions) n'a de sens qu'avec `returnRelatedActions`,
    # que le client n'envoie pas : retiré exprès.
    assert _spec.KEYWORDS_TYPES[entity] == tuple(
        v for v in officiels if v != "relatedActions")


@pytest.mark.parametrize("entity", ENTITIES)
def test_filtres_et_return_more_data_existent_dans_la_reference(entity):
    params = _raml_params(entity)
    assert set(_spec.SEARCH_FILTERS[entity]) <= set(params)
    if _spec.RETURN_MORE_DATA[entity]:
        assert _spec.RETURN_MORE_DATA[entity] == tuple(
            _enum_puces(params["returnMoreData"]))


@pytest.mark.parametrize("entity", ENTITIES)
def test_pagination_de_la_reference(entity):
    params = _raml_params(entity)
    # Le plafond propre à une entité est redéclaré dans sa recherche ; sinon
    # c'est celui du trait `sortablePaginable` (500).
    m = re.search(r"maximum: (\d+)", params.get("maxResults", ""))
    assert _spec.MAX_RESULTS[entity] == (int(m.group(1)) if m else 500)


@pytest.mark.parametrize("entity", ENTITIES)
def test_prefixes_d_id_de_la_reference(entity):
    bloc = _raml_params(entity)["keywords"]
    officiels = set(re.findall(r"`?([A-Z]{2,4})\*\*ID\d\*\*", bloc))
    assert set(_spec.KEYWORD_PREFIXES[entity]) == officiels


# --- 2. ce que le client construit est valide ------------------------------------

VALIDES = [
    ("contacts", {"firstName": "Ada", "lastName": "Lovelace",
                  "email1": "ada@example.com", "civility": 1, "typesOf": ["2"],
                  "origin": {"typeOf": 3, "detail": "salon"},
                  "socialNetworks": [{"network": "linkedin",
                                      "url": "https://linkedin.com/in/x"}]},
     {"company": {"type": "company", "id": 12},
      "influencers": [{"type": "resource", "id": "4"}]}),
    ("companies", {"name": "Acme", "staff": 40, "website": "https://acme.test",
                   "departments": ["Achats"]},
     {"parentCompany": None, "mainManager": {"type": "resource", "id": 5}}),
    ("companies", {"name": "Solo"}, None),
    ("opportunities", {"title": "TMA Java", "typeOf": 1, "startDate": "immediate",
                       "estimatesExcludingTax": 12000.5, "isVisible": True},
     {"company": {"type": "company", "id": 12}, "contact": {"type": "contact",
                                                            "id": 34}}),
    ("opportunities", {"title": "Audit", "startDate": "2026-11-01",
                       "endDate": "2026-12-31"}, None),
    ("actions", {"typeOf": 10, "text": "Appel de qualification",
                 "startDate": "2026-10-02T09:30:00+0200"},
     {"dependsOn": {"type": "contact", "id": 34}}),
    ("actions", {"typeOf": 0, "title": "Point"},
     {"dependsOn": {"type": "opportunity", "id": 7},
      "company": {"type": "company", "id": 12}}),
]


@pytest.mark.parametrize("entity,attrs,rels", VALIDES)
def test_corps_construit_valide_contre_le_schema_officiel(entity, attrs, rels):
    body = build_create_body(entity, attrs, rels)
    _validateur(entity).validate(body)


# --- 3. refus local ⇔ refus du schéma officiel ----------------------------------

def _brut(entity, attrs, rels):
    """Le corps tel qu'on l'enverrait SANS vérification locale."""
    data = {"type": RECORD_TYPES[entity], "attributes": attrs}
    if rels is not None:
        data["relationships"] = {
            k: {"data": (None if v is None else
                         [{"id": str(i["id"]), "type": i["type"]} for i in v]
                         if isinstance(v, list) else
                         {"id": str(v["id"]), "type": v["type"]})}
            for k, v in rels.items()}
    return {"data": data}


_C = {"company": {"type": "company", "id": 1}}
_D = {"dependsOn": {"type": "contact", "id": 1}}
INVALIDES = [
    ("contacts", {"firstName": "A", "lastName": "B", "nickname": "x"}, _C),
    ("contacts", {"firstName": "A", "lastName": "B" * 101}, _C),
    ("contacts", {"firstName": "A", "lastName": "B", "civility": "1"}, _C),
    ("contacts", {"firstName": "A", "lastName": "B", "state": -1}, _C),
    ("contacts", {"firstName": "A", "lastName": "B",
                  "origin": {"typeOf": 3}}, _C),
    ("contacts", {"firstName": "A", "lastName": "B",
                  "socialNetworks": [{"network": "bluesky", "url": "u"}]}, _C),
    ("contacts", {"firstName": "A", "lastName": "B"},
     {"company": {"type": "contact", "id": 1}}),
    ("contacts", {"firstName": "A", "lastName": "B"},
     {**_C, "influencers": {"type": "resource", "id": 1}}),
    ("companies", {"name": "A", "staff": 1.5}, None),
    ("companies", {"name": "A", "billingDetails": []}, None),
    ("companies", {"name": "A"}, {"owner": {"type": "resource", "id": 1}}),
    ("opportunities", {"title": "A", "startDate": "bientôt"}, None),
    ("opportunities", {"title": "A", "typeOf": 0}, None),
    ("opportunities", {"title": "A", "isVisible": "yes"}, None),
    ("actions", {"typeOf": 1, "startDate": "2026-10-02 09:30:00"}, _D),
    ("actions", {"typeOf": 1, "startDate": "2026-10-02T09:30:00+02:00"}, _D),
    ("actions", {"typeOf": 1}, {"dependsOn": {"type": "company", "id": 1}}),
    ("actions", {"typeOf": 1, "description": "x" * 1001}, _D),
]


@pytest.mark.parametrize("entity,attrs,rels", INVALIDES)
def test_refus_local_quand_la_reference_refuse(entity, attrs, rels):
    # `billingDetails` est valide pour l'API mais hors surface : seul cas où le
    # client est plus strict, et c'est voulu.
    if "billingDetails" not in attrs:
        assert not _valide(entity, _brut(entity, attrs, rels)), \
            "le cas n'est pas invalide pour la référence : le test ment"
    with pytest.raises(ValueError):
        build_create_body(entity, attrs, rels)


def test_seule_regle_metier_hors_schema_entreprise_et_contact_ensemble():
    # Écrite dans la description du schéma, pas dans sa grammaire.
    rels = {"company": {"type": "company", "id": 1}}
    assert _valide("opportunities", _brut("opportunities", {"title": "A"}, rels))
    with pytest.raises(ValueError, match="together"):
        build_create_body("opportunities", {"title": "A"}, rels)
