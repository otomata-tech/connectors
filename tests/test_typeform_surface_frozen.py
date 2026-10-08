"""La surface de `TypeformClient` est un CONTRAT, pas un détail.

oto-backend épingle oto-core par tag et importe `oto.tools.typeform` : renommer
une méthode, changer un défaut ou déplacer un symbole hors de `client.py` casse
un consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs
signatures, les noms importables depuis `oto.tools.typeform[.client]`, la
correspondance entre les fonctions de la description et les méthodes du client,
et la taille des modules du paquet.

Posé avec le découpage du client en mixins (`_api/forms.py`, `_api/responses.py`,
`_api/webhooks.py`) et l'ajout des écritures.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la
constante EN CONNAISSANCE DE CAUSE, jamais par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.typeform as pkg
import oto.tools.typeform.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "typeform" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_get': "(self, path: 'str', **params: 'Any') -> 'Any'",
    '_send': "(self, method: 'str', path: 'str', *, json: 'Any' = None) -> 'Any'",
    'create_form': "(self, *, title: 'str', type: 'Optional[str]' = None, settings: 'Optional[Dict[str, Any]]' = None, theme: 'Optional[Dict[str, Any]]' = None, workspace: 'Optional[Dict[str, Any]]' = None, hidden: 'Optional[List[str]]' = None, variables: 'Optional[Dict[str, Any]]' = None, welcome_screens: 'Optional[List[Dict[str, Any]]]' = None, thankyou_screens: 'Optional[List[Dict[str, Any]]]' = None, fields: 'Optional[List[Dict[str, Any]]]' = None, logic: 'Optional[List[Dict[str, Any]]]' = None) -> 'Any'",
    'delete_form': "(self, form_id: 'str') -> 'None'",
    'delete_responses': "(self, form_id: 'str', included_response_ids: 'List[str]') -> 'None'",
    'delete_webhook': "(self, form_id: 'str', tag: 'str') -> 'None'",
    'get_form': "(self, form_id: 'str') -> 'Any'",
    'get_webhook': "(self, form_id: 'str', tag: 'str') -> 'Any'",
    'list_forms': "(self, *, search: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None, workspace_id: 'Optional[str]' = None, sort_by: 'Optional[str]' = None, order_by: 'Optional[str]' = None, is_public: 'Optional[bool]' = None) -> 'Any'",
    'list_responses': "(self, form_id: 'str', *, page_size: 'Optional[int]' = None, since: 'Optional[Union[str, int]]' = None, until: 'Optional[Union[str, int]]' = None, after: 'Optional[str]' = None, before: 'Optional[str]' = None, included_response_ids: 'ListParam' = None, excluded_response_ids: 'ListParam' = None, response_type: 'ListParam' = None, sort: 'Optional[str]' = None, query: 'Optional[str]' = None, fields: 'ListParam' = None, answered_fields: 'ListParam' = None) -> 'Any'",
    'list_webhooks': "(self, form_id: 'str') -> 'Any'",
    'list_workspaces': "(self, *, search: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
    'replace_form': "(self, form_id: 'str', *, title: 'str', type: 'Optional[str]' = None, settings: 'Optional[Dict[str, Any]]' = None, theme: 'Optional[Dict[str, Any]]' = None, workspace: 'Optional[Dict[str, Any]]' = None, hidden: 'Optional[List[str]]' = None, variables: 'Optional[Dict[str, Any]]' = None, welcome_screens: 'Optional[List[Dict[str, Any]]]' = None, thankyou_screens: 'Optional[List[Dict[str, Any]]]' = None, fields: 'Optional[List[Dict[str, Any]]]' = None, logic: 'Optional[List[Dict[str, Any]]]' = None) -> 'Any'",
    'summarize_responses': "(self, form_id: 'str', *, since: 'Optional[Union[str, int]]' = None, until: 'Optional[Union[str, int]]' = None, response_type: 'ListParam' = None, max_pages: 'int' = 10, page_size: 'int' = 1000) -> 'Dict[str, Any]'",
    'update_form': "(self, form_id: 'str', operations: 'List[Dict[str, Any]]') -> 'None'",
    'upsert_webhook': "(self, form_id: 'str', tag: 'str', *, url: 'str', enabled: 'bool', event_types: 'Optional[Dict[str, bool]]' = None, secret: 'Optional[str]' = None, verify_ssl: 'Optional[bool]' = None) -> 'Any'",
}

#: Méthodes publiques que la description ne porte pas, et pourquoi : un corps en
#: tableau JSON Patch ; plusieurs appels et un calcul local.
OUTSIDE_DESCRIPTION = {"update_form", "summarize_responses"}


def _members():
    return {n: str(inspect.signature(getattr(mod.TypeformClient, n)))
            for n in dir(mod.TypeformClient)
            if not n.startswith("__") and callable(getattr(mod.TypeformClient, n))}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_noms_importables():
    assert pkg.__all__ == ["REGIONS", "TypeformClient"]
    for name in ("REGIONS", "TypeformClient", "ListParam", "_clean", "_csv", "_segment"):
        assert hasattr(mod, name), name


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    described = {f["name"] for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert described == public - OUTSIDE_DESCRIPTION


def test_aucun_module_de_500_lignes_ou_plus():
    package = pathlib.Path(mod.__file__).parent
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path
