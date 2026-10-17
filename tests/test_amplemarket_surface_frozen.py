"""La surface de `AmplemarketClient` est un CONTRAT, pas un détail.

oto-backend épingle oto-core par tag et importe `oto.tools.amplemarket` :
renommer une méthode, changer un défaut ou déplacer un symbole casse un
consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC leurs
signatures, les noms importables, la correspondance entre les fonctions de la
description et les méthodes du client, et la taille des modules du paquet.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la
constante EN CONNAISSANCE DE CAUSE, jamais par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.amplemarket as pkg
import oto.tools.amplemarket.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "amplemarket" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_request': "(self, method: 'str', path: 'str', *, params: 'Optional[Dict[str, Any]]' = None, body: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    'add_excluded_domains': "(self, domains: 'List[Any]') -> 'Any'",
    'add_excluded_emails': "(self, emails: 'List[Any]') -> 'Any'",
    'add_leads_to_lead_list': "(self, lead_list_id: 'str', leads: 'List[Dict[str, Any]]', type: 'Optional[str]' = None) -> 'Any'",
    'add_leads_to_sequence': "(self, sequence_id: 'str', leads: 'List[Dict[str, Any]]', settings: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    'cancel_company_enrichment': "(self, request_id: 'str') -> 'Any'",
    'cancel_email_validation': "(self, validation_id: 'str') -> 'Any'",
    'cancel_person_enrichment': "(self, request_id: 'str') -> 'Any'",
    'complete_task': "(self, task_id: 'str', set_lead_to_completed: 'Optional[bool]' = None) -> 'Any'",
    'create_contact': "(self, email: 'str', first_name: 'Optional[str]' = None, last_name: 'Optional[str]' = None, title: 'Optional[str]' = None, city: 'Optional[str]' = None, state: 'Optional[str]' = None, country: 'Optional[str]' = None, industry: 'Optional[str]' = None, linkedin_url: 'Optional[str]' = None, owner_email: 'Optional[str]' = None, account_id: 'Optional[str]' = None, crm_account_id: 'Optional[str]' = None, company_domain: 'Optional[str]' = None, company_name: 'Optional[str]' = None, phone_numbers: 'Optional[List[Dict[str, str]]]' = None) -> 'Any'",
    'create_lead_list': "(self, owner: 'str', type: 'str', leads: 'List[Dict[str, Any]]', shared: 'bool' = False, name: 'Optional[str]' = None, visible: 'Optional[bool]' = None, options: 'Optional[Dict[str, bool]]' = None) -> 'Any'",
    'find_company': "(self, domain: 'Optional[str]' = None, linkedin_url: 'Optional[str]' = None) -> 'Any'",
    'find_person': "(self, email: 'Optional[str]' = None, linkedin_url: 'Optional[str]' = None, name: 'Optional[str]' = None, title: 'Optional[str]' = None, company_name: 'Optional[str]' = None, company_domain: 'Optional[str]' = None, reveal_email: 'Optional[bool]' = None, reveal_phone_numbers: 'Optional[bool]' = None) -> 'Any'",
    'get_account': "(self, account_id: 'str') -> 'Any'",
    'get_account_info': "(self) -> 'Any'",
    'get_company_enrichment': "(self, request_id: 'str') -> 'Any'",
    'get_contact': "(self, contact_id: 'str') -> 'Any'",
    'get_contact_by_email': "(self, email: 'str') -> 'Any'",
    'get_email_validation': "(self, validation_id: 'str', page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'get_job_opening': "(self, job_opening_id: 'str') -> 'Any'",
    'get_lead_list': "(self, lead_list_id: 'str', page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'get_person_enrichment': "(self, request_id: 'str') -> 'Any'",
    'list_accounts': "(self, name: 'Optional[str]' = None, domain: 'Optional[str]' = None, owner_email: 'Optional[str]' = None, tags: 'Optional[List[str]]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None) -> 'Any'",
    'list_call_dispositions': "(self) -> 'Any'",
    'list_calls': "(self, user_id: 'Optional[str]' = None, from_number: 'Optional[str]' = None, to_number: 'Optional[str]' = None, direction: 'Optional[str]' = None, start_date_from: 'Optional[str]' = None, start_date_to: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_contacts': "(self, ids: 'Optional[List[str]]' = None, name: 'Optional[str]' = None, account_id: 'Optional[str]' = None) -> 'Any'",
    'list_excluded_domains': "(self, domain: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_excluded_emails': "(self, email: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_job_openings': "(self, company_id: 'Optional[str]' = None, domain: 'Optional[str]' = None, linkedin_url: 'Optional[str]' = None, person_seniorities: 'Optional[List[str]]' = None, person_departments: 'Optional[List[str]]' = None, person_job_functions: 'Optional[List[str]]' = None, only_remote: 'Optional[bool]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None) -> 'Any'",
    'list_lead_lists': "(self, status: 'Optional[str]' = None, owner_id: 'Optional[str]' = None, owner_email: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_mailboxes': "(self, status: 'Optional[str]' = None, email_provider: 'Optional[str]' = None, user_email: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_personas': "(self, name: 'Optional[str]' = None, enabled: 'Optional[bool]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None) -> 'Any'",
    'list_sequences': "(self, status: 'Optional[str]' = None, name: 'Optional[str]' = None, created_by_user_id: 'Optional[str]' = None, created_by_user_email: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_task_statuses': "(self) -> 'Any'",
    'list_task_types': "(self) -> 'Any'",
    'list_tasks': "(self, user_id: 'str', status: 'Optional[str]' = None, type: 'Optional[str]' = None, automatic: 'Optional[bool]' = None, sequence_key: 'Optional[str]' = None, keyword: 'Optional[str]' = None, contact_local_time_from: 'Optional[int]' = None, contact_local_time_to: 'Optional[int]' = None, message_opens: 'Optional[int]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'list_territories': "(self, name: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None) -> 'Any'",
    'list_users': "(self, status: 'Optional[str]' = None, role: 'Optional[str]' = None, email: 'Optional[str]' = None, page_size: 'Optional[int]' = None, page_after: 'Optional[str]' = None, page_before: 'Optional[str]' = None) -> 'Any'",
    'log_call': "(self, task_id: 'str', user_id: 'str', from_number: 'str', to_number: 'str', duration: 'int', answered: 'bool', human: 'bool', disposition_id: 'Optional[str]' = None, transcription: 'Optional[str]' = None, recording_url: 'Optional[str]' = None) -> 'Any'",
    'probe': "(self) -> 'Any'",
    'remove_excluded_domains': "(self, domains: 'List[Any]') -> 'Any'",
    'remove_excluded_emails': "(self, emails: 'List[Any]') -> 'Any'",
    'review_phone_number': "(self, phone_number_id: 'str', user_id: 'str', reason: 'str' = 'wrong_number') -> 'Any'",
    'search_companies': "(self, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None, **filters: 'Any') -> 'Any'",
    'search_people': "(self, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None, **filters: 'Any') -> 'Any'",
    'skip_task': "(self, task_id: 'str') -> 'Any'",
    'start_company_enrichment': "(self, companies: 'List[Dict[str, Any]]') -> 'Any'",
    'start_email_validation': "(self, emails: 'List[Any]') -> 'Any'",
    'start_person_enrichment': "(self, leads: 'List[Dict[str, Any]]', reveal_email: 'Optional[bool]' = None, reveal_phone_numbers: 'Optional[bool]' = None) -> 'Any'",
    'update_mailbox_daily_limit': "(self, mailbox_id: 'str', daily_email_limit: 'int') -> 'Any'",
}

#: Méthode publique que la description ne porte pas, et pourquoi.
#: `probe` est une commodité de vérification de clé, pas une fonction exposée.
OUTSIDE_DESCRIPTION = {"probe"}


def _members():
    return {n: str(inspect.signature(getattr(mod.AmplemarketClient, n)))
            for n in dir(mod.AmplemarketClient)
            if not n.startswith("__") and callable(getattr(mod.AmplemarketClient, n))}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_noms_importables():
    assert pkg.__all__ == ["AmplemarketClient", "next_page_after"]
    for name in ("AmplemarketClient", "next_page_after", "BASE_URL", "HTTP_TIMEOUT",
                 "RETRY_STATUSES", "MAX_ATTEMPTS"):
        assert hasattr(mod, name), name


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    described = {f["name"] for f in yaml.safe_load(
        DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert described == public - OUTSIDE_DESCRIPTION


def test_aucun_module_de_500_lignes_ou_plus():
    package = pathlib.Path(mod.__file__).parent
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path
