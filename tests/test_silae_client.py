"""SilaeClient — routes, request envelopes and period formats checked against the
Silae function pages and OpenAPI file; token in the body and cached per credential;
errors raised, never returned as a dict; documents decoded from base64.

Transport stubbed (a fake `requests.Session`): no network, no real credential.
"""
import base64
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.silae import SilaeAuthError, SilaeClient
from oto.tools.silae import auth as silae_auth
from oto.tools.silae.periodes import periode_date, periode_datetime, plage

BASE = "https://payroll-api.silae.fr/payroll/"


class _Resp:
    def __init__(self, payload=None, status_code=200, raw=None):
        self.status_code = status_code
        self._payload = payload
        if raw is not None:
            self.content = raw
        else:
            self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return json.loads(self.content)  # ValueError on a body that is not JSON

    @property
    def text(self):
        return self.content.decode(errors="replace")


class _Session:
    """Records every call. `reponses` = queue of API responses (default: `{}`)."""

    def __init__(self, reponses=None, token=None):
        self.reponses = list(reponses or [])
        self.token = token or _Resp({"access_token": "tok", "expires_in": 3600})
        self.tokens = []
        self.appels = []

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        assert "params" not in kw, "a secret never goes in the query string"
        self.tokens.append({"url": url, "data": data})
        return self.token

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.appels.append({"method": method, "url": url, "json": json, "params": params,
                            "headers": headers})
        return self.reponses.pop(0) if self.reponses else _Resp({})


@pytest.fixture(autouse=True)
def _cache_vide():
    silae_auth._TOKEN_CACHE.clear()
    yield
    silae_auth._TOKEN_CACHE.clear()


def _client(*reponses, token=None):
    c = SilaeClient(client_id="cid", client_secret="csec", subscription_key="sub")
    c.session = _Session(reponses, token=token)
    return c


def _dernier(c):
    a = c.session.appels[-1]
    return a["url"].removeprefix(BASE), a["json"], a["headers"]


# --- periods -------------------------------------------------------------------

def test_period_formats_follow_the_field_type():
    assert periode_datetime("2026-05") == "2026-05-01T00:00:00"
    assert periode_date("2026-05") == "2026-05-01"
    # What Silae itself returns (current period) can be passed back as is.
    assert periode_datetime("2026-05-01T00:00:00") == "2026-05-01T00:00:00"
    assert periode_date("2026-05-01") == "2026-05-01"


@pytest.mark.parametrize("bad", ["2026-5", "202605", "2026-13", "2026-05-14", "mai 2026", ""])
def test_a_period_that_is_not_a_month_is_refused(bad):
    with pytest.raises(ValueError, match="AAAA-MM"):
        periode_datetime(bad)


def test_range_is_checked_and_bounded_to_twelve_months_when_asked():
    assert plage("2026-01", "2026-12", max_mois=12) == ("2026-01-01T00:00:00",
                                                       "2026-12-01T00:00:00")
    with pytest.raises(ValueError, match="13 months"):
        plage("2026-01", "2027-01", max_mois=12)
    with pytest.raises(ValueError, match="before"):
        plage("2026-05", "2026-04")


# --- auth & transport ----------------------------------------------------------

def test_missing_secret_raises():
    with pytest.raises(MissingCredential):
        SilaeClient(client_id="cid", client_secret="csec", subscription_key=None)


def test_token_secrets_go_in_the_body_and_the_token_is_cached_per_credential():
    c = _client()
    c.list_dossiers()
    c.list_dossiers()
    assert len(c.session.tokens) == 1
    data = c.session.tokens[0]["data"]
    assert data["grant_type"] == "client_credentials" and data["client_secret"] == "csec"
    assert data["scope"].endswith("/.default")
    # A client built for the NEXT call reuses the process-wide cache.
    c2 = _client()
    c2.list_dossiers()
    assert c2.session.tokens == []


def test_headers_carry_bearer_subscription_key_and_dossier():
    c = _client()
    c.dossier_periode_en_cours("001")
    _, body, headers = _dernier(c)
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Ocp-Apim-Subscription-Key"] == "sub"
    assert headers["dossiers"] == "001"
    assert body == {"numeroDossier": "001"}


def test_a_call_without_dossier_still_sends_an_empty_dossiers_header():
    c = _client()
    c.list_dossiers()
    _, _, headers = _dernier(c)
    assert headers["dossiers"] == ""


def test_a_refused_credential_raises_a_401_without_echoing_the_description():
    c = _client(token=_Resp({"error": "invalid_client",
                              "error_description": "The client id 'cid' …"}, 400))
    with pytest.raises(SilaeAuthError) as e:
        c.list_dossiers()
    assert e.value.status_code == 401
    assert "cid" not in str(e.value)


def test_an_api_error_raises_with_silae_body():
    erreur = {"errors": [{"code": "1011", "message": "la valeur de la liste de dossiers "
                          "est nulle ou vide", "metadata": None}],
              "recoverable": True, "source": "Api"}
    c = _client(_Resp(erreur, 400))
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_etablissements("001")
    assert e.value.status_code == 400 and e.value.body == erreur


def test_a_401_refreshes_the_token_once_then_raises():
    c = _client(_Resp({"x": 1}, 401), _Resp({"listeDossiers": []}))
    assert c.list_dossiers() == {"listeDossiers": []}
    assert len(c.session.tokens) == 2
    c = _client(_Resp({}, 401), _Resp({}, 401))
    silae_auth._TOKEN_CACHE.clear()
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_dossiers()
    assert e.value.status_code == 401


def test_a_200_that_is_not_json_raises():
    c = _client(_Resp(raw=b"<html>"))
    with pytest.raises(UpstreamHTTPError):
        c.list_dossiers()


# --- routes and bodies ---------------------------------------------------------

@pytest.mark.parametrize("appel,route,corps", [
    (lambda c: c.list_dossiers(), "v1/InfosTechniquesDossiers/ListeDossiers",
     {"typeDossiers": 0}),
    (lambda c: c.list_numeros_dossiers(), "v1/InfosTechniquesDossiers/ListeNumerosDossiers", {}),
    (lambda c: c.list_conventions_collectives(),
     "v1/InfosTechniquesDossiers/ListeInformationsDossiersPaie", {}),
    (lambda c: c.dossier_periode_en_cours("001"),
     "v1/InfosTechniquesDossiers/DossierRecupererPeriodeEnCours", {"numeroDossier": "001"}),
    (lambda c: c.list_etablissements("001"), "v1/FicheSociete/ListeEtablissementsDossierPaie",
     {"numeroDossier": "001"}),
    (lambda c: c.list_organismes("001", code_nature="SSOC"), "v1/Organisme/ListeOrganismes",
     {"numeroDossier": "001", "codeNature": "SSOC"}),
    (lambda c: c.list_salaries("001", actif_sur_periode="2026-05"),
     "v1/InfosSalaries/ListeSalaries",
     {"numeroDossier": "001",
      "listeSalariesOptions": {"optionActifSurPeriode": "2026-05-01T00:00:00"}}),
    (lambda c: c.list_salaries("001"), "v1/InfosSalaries/ListeSalaries",
     {"numeroDossier": "001"}),
    (lambda c: c.lecture_informations_salarie("001", "0001"),
     "v1/InfosSalaries/LectureInformationsSalarie",
     {"matricule": "0001", "numeroDossier": "001"}),
    (lambda c: c.list_salarie_emplois("001", "0001", 1), "v1/SalarieEmplois/ListeSalarieEmplois",
     {"typeEmplois": 1, "matriculeSalarie": "0001", "numeroDossier": "001"}),
    (lambda c: c.matricule_depuis_interne("001", "INT-7"),
     "v1/InfosTechniquesDossiers/MatriculeSalarie",
     {"matriculeInterneSalarie": "INT-7", "numeroDossier": "001"}),
    (lambda c: c.list_variables_a_saisir("001"), "v1/VariablesASaisir/ListeVariablesASaisir",
     {"numeroDossier": "001"}),
    (lambda c: c.bulletins_ids_pdf("001", periode_debut="2026-01", periode_fin="2026-03"),
     "v1/InfosSalaries/SalariesBulletins",
     {"requeteSalariesBulletins": {"matriculeSalarie": "", "identifiantEmploi": 0,
                                   "periodeDebut": "2026-01-01T00:00:00",
                                   "periodeFin": "2026-03-01T00:00:00"},
      "numeroDossier": "001"}),
    (lambda c: c.bulletins_indices("001", "0001", identifiant_emploi=4, periode_debut="2026-05",
                                   periode_fin="2026-05", originaux_seulement=True),
     "v1/InfosBulletins/SalarieBulletinsIndices",
     {"requeteSalariesBulletins": {"matriculeSalarie": "0001", "identifiantEmploi": 4,
                                   "periodeDebut": "2026-05-01T00:00:00",
                                   "periodeFin": "2026-05-01T00:00:00",
                                   "bulletinsOriginauxSeulement": True},
      "numeroDossier": "001"}),
    (lambda c: c.bulletin_entete("001", "0001", identifiant_emploi=4, periode="2026-05",
                                 indice_periode=1),
     "v1/InfosBulletins/SalarieBulletinEntete",
     {"requeteSalarieBulletinEntete": {"matriculeSalarie": "0001", "identifiantEmploi": 4,
                                       "periode": "2026-05-01T00:00:00", "indicePeriode": 1},
      "numeroDossier": "001"}),
    (lambda c: c.bulletin_lignes("001", "0001", identifiant_emploi=4, periode="2026-05"),
     "v1/InfosBulletins/SalarieBulletinLignes",
     {"requeteSalarieBulletinLignes": {"matriculeSalarie": "0001", "identifiantEmploi": 4,
                                       "periode": "2026-05-01T00:00:00"},
      "numeroDossier": "001"}),
    (lambda c: c.bulletin_lignes_filtrees("001", "0001", identifiant_emploi=4, periode="2026-05",
                                          filtres={"zone": 3, "code_ducs": "100%",
                                                   "exclure_lignes_neutres": True}),
     "v1/InfosBulletins/SalarieBulletinLignesSelonFiltres",
     {"requeteSalarieBulletinLignes": {"matriculeSalarie": "0001", "identifiantEmploi": 4,
                                       "periode": "2026-05-01T00:00:00"},
      "requeteSalarieBulletinLignesFiltres": {"zone": 3, "codeDucs": "100%",
                                              "exclureLignesNeutres": True},
      "numeroDossier": "001"}),
    (lambda c: c.bulletin_details("001", "0001", identifiant_emploi=4, periode="2026-05",
                                  type_details=2, filtres={"zone": 3}),
     "v1/InfosBulletins/SalarieBulletinDetails",
     {"requeteSalarieBulletinDetails": {"typeDetails": 2, "matriculeSalarie": "0001",
                                        "identifiantEmploi": 4,
                                        "periode": "2026-05-01T00:00:00"},
      "requeteSalarieBulletinFiltres": {"zone": 3}, "numeroDossier": "001"}),
    (lambda c: c.bulletin_cumuls("001", "0001", periode_debut="2026-01", periode_fin="2026-12"),
     "v1/InfosBulletins/SalarieBulletinCumuls",
     {"periodeDebut": "2026-01-01T00:00:00", "periodeFin": "2026-12-01T00:00:00",
      "matriculeSalarie": "0001", "numeroDossier": "001"}),
    (lambda c: c.list_dsn_mensuelles("001", periode="2026-05"),
     "v1/InfosDeclaration/ListeDSNMensuelles", {"periode": "2026-05-01", "numeroDossier": "001"}),
    (lambda c: c.etat_declarations("001", type_declaration="DECLARATION", periode="2026-05"),
     "v1/EtatDeclaration/EtatDeclarations",
     {"typeDeclaration": "DECLARATION", "periode": "2026-05-01T00:00:00",
      "numeroDossier": "001"}),
])
def test_each_function_posts_its_documented_route_and_body(appel, route, corps):
    c = _client()
    appel(c)
    path, body, _ = _dernier(c)
    assert c.session.appels[-1]["method"] == "POST"
    assert path == route
    assert body == corps


def test_cumulative_totals_refuse_more_than_twelve_months_before_calling():
    c = _client()
    with pytest.raises(ValueError, match="12"):
        c.bulletin_cumuls("001", "0001", periode_debut="2025-01", periode_fin="2026-01")
    assert c.session.appels == []


def test_a_single_payslip_needs_a_matricule():
    c = _client()
    with pytest.raises(ValueError, match="matricule"):
        c.bulletin_entete("001", "", identifiant_emploi=1, periode="2026-05")
    assert c.session.appels == []


def test_an_unknown_line_filter_or_zone_is_refused():
    c = _client()
    with pytest.raises(ValueError, match="unknown line filter"):
        c.bulletin_lignes_filtrees("001", "0001", identifiant_emploi=1, periode="2026-05",
                                   filtres={"codeducs": "100"})
    with pytest.raises(ValueError, match="zone"):
        c.bulletin_details("001", "0001", identifiant_emploi=1, periode="2026-05",
                           filtres={"zone": 9})
    assert c.session.appels == []


def test_no_declaration_found_is_an_empty_list():
    c = _client(_Resp(raw=b"null"))  # Silae's documented "no declaration found"
    assert c.etat_declarations("001", type_declaration="DUE") == {"etatDeclarations": []}


# --- documents -----------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_charges_table_body_and_decoded_document():
    c = _client(_Resp({"document": _b64(b"PK\x03\x04xlsx")}))
    doc = c.edition_tableau_charges("001", periode_debut="2026-01", periode_fin="2026-03",
                                    format="xlsx")
    path, body, _ = _dernier(c)
    assert path == "v1/EditionEtatsPaie/EditionTableauDesCharges"
    assert body == {"format": "ExcelXLSX", "periodeDebut": "2026-01-01T00:00:00",
                    "periodeFin": "2026-03-01T00:00:00", "numeroDossier": "001"}
    assert doc["data"] == b"PK\x03\x04xlsx"
    assert doc["filename"].endswith(".xlsx") and "spreadsheetml" in doc["mimetype"]


def test_contributions_detail_body_is_bounded_and_named_as_in_the_openapi_file():
    c = _client(_Resp({"document": _b64(b"%PDF-1.7")}))
    c.edition_detail_cotisations("001", periode_debut="2026-01", periode_fin="2026-06",
                                 detail_salaries=True, grouper_par="mensuel")
    path, body, _ = _dernier(c)
    assert path == "v1/EditionEtatsPaie/EditionDetailDesCotisations"
    assert body == {"format": "Pdf", "periodeDebut": "2026-01-01T00:00:00",
                    "periodeFin": "2026-06-01T00:00:00", "numeroDossier": "001",
                    "grouperPar": "Mensuel", "detailSalaries": True}
    with pytest.raises(ValueError, match="12"):
        c.edition_detail_cotisations("001", periode_debut="2025-01", periode_fin="2026-06")


def test_async_report_starts_then_polls_with_a_get_and_the_task_in_the_query():
    c = _client(_Resp({"guidTache": "abcd-1234"}),
                _Resp({"statut": "ETAT_ENCOURS", "progression": 40.0}),
                _Resp({"statut": "ETAT_TERMINEE", "document": _b64(b"%PDF-1.7 x")}))
    assert c.lancer_edition_tableau_charges(
        "001", periode_debut="2026-05", periode_fin="2026-05") == {"guidTache": "abcd-1234"}
    assert _dernier(c)[0] == "v1/EditionEtatsPaie/EditionTableauDesChargesAsynchrone"
    en_cours = c.statut_edition("tableau_charges", "abcd-1234", numero_dossier="001")
    a = c.session.appels[-1]
    assert a["method"] == "GET" and a["json"] is None
    assert a["url"].removeprefix(BASE) == "v1/EditionEtatsPaie/StatutEditionTableauDesChargesAsynchrone"
    assert a["params"] == {"guidTache": "abcd-1234"}
    assert en_cours["statut"] == "ETAT_ENCOURS" and en_cours["document"] is None
    fini = c.statut_edition("tableau_charges", "abcd-1234", numero_dossier="001")
    assert fini["document"]["mimetype"] == "application/pdf"


def test_declaration_summaries_use_the_declaration_pdf_family_and_a_date():
    c = _client(_Resp({"document": _b64(b"%PDF")}), _Resp({"guidTache": "g"}), _Resp({}))
    c.recap_declarations("001", periode="2026-05")
    assert _dernier(c)[:2] == ("v1/DeclarationPDF/RecupererDeclarations",
                               {"periode": "2026-05-01", "numeroDossier": "001"})
    c.lancer_recap_declarations("001", periode="2026-05")
    assert _dernier(c)[0] == "v1/DeclarationPDF/RecupererDeclarationsAsynchrone"
    c.statut_edition("recap_declarations", "g")
    assert _dernier(c)[0] == "v1/DeclarationPDF/StatutRecupererDeclarationsAsynchrone"


def test_partial_dsn_sends_segments_as_a_list_and_decodes_the_content():
    c = _client(_Resp({"contenuPartiel": _b64(b"S21.G00.30.001,'1'\n")}))
    doc = c.contenu_partiel_dsn("001", periode="2026-05", etablissement="SIEGE", type_dsn=1,
                                fraction=1, segments=["S21.G00.30"], code_organisme="URSSAF")
    path, body, _ = _dernier(c)
    assert path == "v1/InfosDeclaration/AcquisitionContenuPartielDSN"
    assert body == {"nomInterneEtablissement": "SIEGE", "typeDSN": 1, "fraction": 1,
                    "periode": "2026-05-01", "numeroDossier": "001",
                    "segments": ["S21.G00.30"], "codeOrganisme": "URSSAF"}
    assert doc["data"] == b"S21.G00.30.001,'1'\n"
    with pytest.raises(ValueError, match="segments"):
        c.contenu_partiel_dsn("001", periode="2026-05", etablissement="SIEGE", type_dsn=1,
                              fraction=1, segments="S21.G00.30")


def test_a_document_that_is_missing_or_not_base64_raises():
    c = _client(_Resp({"document": ""}), _Resp({"document": "%%%"}))
    with pytest.raises(UpstreamHTTPError):
        c.recap_declarations("001", periode="2026-05")
    with pytest.raises(UpstreamHTTPError):
        c.recap_declarations("001", periode="2026-05")


# --- writes: documented nested bodies (not exposed by any consumer here) ------------

def test_writes_use_the_documented_families_and_nested_bodies():
    c = _client()
    c.ajouter_element_variable("001", "0001", periode="2026-05", code="EV1", montant=2.5)
    assert _dernier(c)[:2] == ("v1/ElementsVariables/SalarieAjouterElementVariable", {
        "elementVariable": {"periodeElementVariable": "2026-05-01T00:00:00",
                            "codeElementVariable": "EV1", "montantElementVariable": 2.5},
        "matriculeSalarie": "0001", "numeroDossier": "001"})
    c.ajouter_prime("001", "0001", periode="2026-05", code="P1", montant=100.0)
    assert _dernier(c)[:2] == ("v1/ElementsVariables/SalarieAjouterPrime", {
        "prime": {"periodePrime": "2026-05-01T00:00:00", "codePrime": "P1",
                  "montantPrime": 100.0},
        "matriculeSalarie": "0001", "numeroDossier": "001"})
    c.ajouter_heures("001", "0001", periode="2026-05", code="H25", nombre=4, ajouter=True)
    assert _dernier(c)[:2] == ("v1/ActivitesEtHeures/SalarieAjouterHeures", {
        "heures": {"periodeHeures": "2026-05-01T00:00:00", "codeHeures": "H25",
                   "nombreHeures": 4, "ajouter": True},
        "matriculeSalarie": "0001", "numeroDossier": "001"})
    c.confirmer_saisies("001", periode="2026-05", confirmer_heures=True, confirmer_primes=False)
    assert _dernier(c)[:2] == ("v1/ElementsVariables/SalariesConfirmerSaisies", {
        "confirmationSaisies": {"periodeConfirmation": "2026-05-01T00:00:00",
                                "confirmerHeures": True, "confirmerPrimes": False},
        "numeroDossier": "001"})
    with pytest.raises(ValueError, match="exactly one"):
        c.ajouter_prime("001", "0001", periode="2026-05", code="P1")


def test_forbidden_functions_are_not_implemented():
    """Payslip control, DSN configuration, pay cycle and show-business computation
    change payroll state: none of them exists in this client."""
    import inspect
    from oto.tools.silae import client as module
    from oto.tools.silae import _api

    sources = inspect.getsource(module) + "".join(
        inspect.getsource(m) for m in (_api.bulletins, _api.declarations, _api.dossiers,
                                       _api.editions, _api.saisies, _api.salaries))
    for interdit in ("ControlerBulletinsPeriode", "ImportXmlParametrageOrganismeDSN",
                     "ActivationDSN", "GererCycleDePaie", "CalculerBulletin"):
        assert interdit not in sources
