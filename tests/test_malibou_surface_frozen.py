"""La surface de `MalibouClient` est un CONTRAT, pas un détail.

oto-backend épingle oto-core par tag et importe `oto.tools.malibou` : renommer une
méthode, changer un défaut ou déplacer un symbole hors de `client.py` casse un
consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs
signatures, les noms importables depuis `oto.tools.malibou[.client]`, la
correspondance entre les fonctions de la description et les méthodes du client,
et la taille des modules du paquet.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la
constante EN CONNAISSANCE DE CAUSE, jamais par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.malibou as pkg
import oto.tools.malibou.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "malibou" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_choice': "(name: 'str', value: 'Optional[str]', allowed: 'tuple') -> 'Optional[str]'",
    '_day': "(name: 'str', value: 'Any') -> 'str'",
    '_get': "(self, path: 'str', params: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    '_path_id': "(name: 'str', value: 'Any') -> 'str'",
    '_positive': "(name: 'str', value: 'Optional[int]') -> 'Optional[int]'",
    '_window': "(start_date: 'Any', end_date: 'Any') -> 'Dict[str, str]'",
    'get_collaborator': "(self, collaborator_id: 'str', establishment_id: 'Optional[str]' = None) -> 'Any'",
    'list_absences': "(self, start_date: 'str', end_date: 'str', *, status: 'Optional[str]' = None, establishment_id: 'Optional[str]' = None, page: 'Optional[int]' = None, limit: 'Optional[int]' = None) -> 'Any'",
    'list_collaborators': "(self, establishment_id: 'Optional[str]' = None, contract_status: 'Optional[str]' = None, page: 'Optional[int]' = None, limit: 'Optional[int]' = None) -> 'Any'",
    'list_work_locations': "(self, start_date: 'str', end_date: 'str', *, status: 'Optional[str]' = None, establishment_id: 'Optional[str]' = None, page: 'Optional[int]' = None, limit: 'Optional[int]' = None) -> 'Any'",
    'probe': "(self) -> 'Any'",
}

#: Méthode publique que la description ne porte pas, et pourquoi.
#: `probe` est une commodité de vérification de clé, pas une fonction exposée.
OUTSIDE_DESCRIPTION = {"probe"}


def _members():
    return {n: str(inspect.signature(getattr(mod.MalibouClient, n)))
            for n in dir(mod.MalibouClient)
            if not n.startswith("__") and callable(getattr(mod.MalibouClient, n))}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_noms_importables():
    assert pkg.__all__ == ["MalibouClient"]
    for name in ("MalibouClient", "BASE_URL", "CONTRACT_STATUSES", "RECORD_STATUSES",
                 "RETRY_STATUSES", "MAX_ATTEMPTS", "HTTP_TIMEOUT"):
        assert hasattr(mod, name), name


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    described = {f["name"] for f in yaml.safe_load(
        DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert described == public - OUTSIDE_DESCRIPTION


def test_les_arguments_decrits_sont_ceux_de_la_methode():
    for fn in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]:
        params = set(inspect.signature(getattr(mod.MalibouClient, fn["name"])).parameters)
        assert set(fn["input"]["properties"]) == params - {"self"}, fn["name"]


def test_aucun_module_de_500_lignes_ou_plus():
    package = pathlib.Path(mod.__file__).parent
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path
