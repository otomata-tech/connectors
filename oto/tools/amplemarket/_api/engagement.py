"""Engagement: lead lists, sequences, contacts, tasks, calls, exclusion lists
and phone-number review."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .shapes import items, page_params, path_id

#: How the leads of a lead list are identified.
LEAD_LIST_TYPES = ("linkedin", "email", "titles_and_company")


def _lead_list_type(value: Optional[str]) -> Optional[str]:
    if value is not None and value not in LEAD_LIST_TYPES:
        raise ValueError(f"type must be one of {', '.join(LEAD_LIST_TYPES)}")
    return value


class EngagementMixin:
    """Needs `_request` (see `AmplemarketClient`)."""

    # --- lead lists -------------------------------------------------------------

    def list_lead_lists(self, status: Optional[str] = None,
                        owner_id: Optional[str] = None,
                        owner_email: Optional[str] = None,
                        page_size: Optional[int] = None,
                        page_after: Optional[str] = None,
                        page_before: Optional[str] = None) -> Any:
        """GET /lead-lists — the lead lists."""
        return self._request("GET", "/lead-lists", params={
            "status": status, "owner_id": owner_id, "owner_email": owner_email,
            **page_params(page_size, page_after, page_before)})

    def get_lead_list(self, lead_list_id: str, page_size: Optional[int] = None,
                      page_after: Optional[str] = None,
                      page_before: Optional[str] = None) -> Any:
        """GET /lead-lists/{id} — one lead list with a page of its leads."""
        return self._request(
            "GET", f"/lead-lists/{path_id('lead_list_id', lead_list_id)}",
            params=page_params(page_size, page_after, page_before))

    def create_lead_list(self, owner: str, type: str, leads: List[Dict[str, Any]],
                         shared: bool = False, name: Optional[str] = None,
                         visible: Optional[bool] = None,
                         options: Optional[Dict[str, bool]] = None) -> Any:
        """POST /lead-lists — create a lead list from <= 10,000 leads, owned by
        the user whose email is `owner`. `options` (`reveal_phone_numbers`,
        `validate_email`, `enrich`) consume credits. Answers 202: the list is
        processed asynchronously (`status`)."""
        if not leads:
            raise ValueError("leads must be a non-empty list")
        return self._request("POST", "/lead-lists", body={
            "name": name, "shared": shared, "visible": visible, "owner": owner,
            "type": _lead_list_type(type), "options": options, "leads": leads})

    def add_leads_to_lead_list(self, lead_list_id: str, leads: List[Dict[str, Any]],
                               type: Optional[str] = None) -> Any:
        """POST /lead-lists/{id}/leads — add <= 10,000 leads (a list holds
        60,000 at most). Answers `total` and `total_added_to_lead_list`."""
        if not leads:
            raise ValueError("leads must be a non-empty list")
        return self._request(
            "POST", f"/lead-lists/{path_id('lead_list_id', lead_list_id)}/leads",
            body={"type": _lead_list_type(type), "leads": leads})

    # --- sequences --------------------------------------------------------------

    def list_sequences(self, status: Optional[str] = None, name: Optional[str] = None,
                       created_by_user_id: Optional[str] = None,
                       created_by_user_email: Optional[str] = None,
                       page_size: Optional[int] = None,
                       page_after: Optional[str] = None,
                       page_before: Optional[str] = None) -> Any:
        """GET /sequences — the sequences (`name` matches case-insensitively)."""
        return self._request("GET", "/sequences", params={
            "status": status, "name": name,
            "created_by_user_id": created_by_user_id,
            "created_by_user_email": created_by_user_email,
            **page_params(page_size, page_after, page_before)})

    def add_leads_to_sequence(self, sequence_id: str, leads: List[Dict[str, Any]],
                              settings: Optional[Dict[str, Any]] = None) -> Any:
        """POST /sequences/{id}/leads — enroll leads (`{email | linkedin_url,
        data?, routing?, overrides?}`) in a sequence: an active sequence starts
        contacting them. Answers the counts added and skipped (duplicates,
        exclusion list, recently contacted, already in a sequence). 30 calls
        per minute."""
        if not leads:
            raise ValueError("leads must be a non-empty list")
        return self._request(
            "POST", f"/sequences/{path_id('sequence_id', sequence_id)}/leads",
            body={"leads": leads, "settings": settings})

    # --- contacts ---------------------------------------------------------------

    def get_contact(self, contact_id: str) -> Any:
        """GET /contacts/{id} — one contact."""
        return self._request("GET", f"/contacts/{path_id('contact_id', contact_id)}")

    def get_contact_by_email(self, email: str) -> Any:
        """GET /contacts/email/{email} — one contact by email."""
        return self._request("GET", f"/contacts/email/{path_id('email', email)}")

    def list_contacts(self, ids: Optional[List[str]] = None, name: Optional[str] = None,
                      account_id: Optional[str] = None) -> Any:
        """GET /contacts — contacts by id (<= 20), or by name and/or Prospect
        Hub account."""
        if ids and len(ids) > 20:
            raise ValueError("ids takes at most 20 contact ids")
        if not (ids or name or account_id):
            raise ValueError("list_contacts needs ids, name or account_id")
        return self._request("GET", "/contacts", params={
            "ids[]": ids, "name": name, "account_id": account_id})

    def create_contact(self, email: str, first_name: Optional[str] = None,
                       last_name: Optional[str] = None, title: Optional[str] = None,
                       city: Optional[str] = None, state: Optional[str] = None,
                       country: Optional[str] = None, industry: Optional[str] = None,
                       linkedin_url: Optional[str] = None,
                       owner_email: Optional[str] = None,
                       account_id: Optional[str] = None,
                       crm_account_id: Optional[str] = None,
                       company_domain: Optional[str] = None,
                       company_name: Optional[str] = None,
                       phone_numbers: Optional[List[Dict[str, str]]] = None) -> Any:
        """POST /contacts — create a contact (409 if the email exists). The
        Prospect Hub account is `account_id`, else resolved from
        `crm_account_id`, `company_domain`, then `company_name`."""
        if not email:
            raise ValueError("email is required")
        return self._request("POST", "/contacts", body={
            "email": email, "first_name": first_name, "last_name": last_name,
            "title": title, "city": city, "state": state, "country": country,
            "industry": industry, "linkedin_url": linkedin_url,
            "owner_email": owner_email, "account_id": account_id,
            "crm_account_id": crm_account_id, "company_domain": company_domain,
            "company_name": company_name, "phone_numbers": phone_numbers})

    # --- tasks ------------------------------------------------------------------

    def list_tasks(self, user_id: str, status: Optional[str] = None,
                   type: Optional[str] = None, automatic: Optional[bool] = None,
                   sequence_key: Optional[str] = None, keyword: Optional[str] = None,
                   contact_local_time_from: Optional[int] = None,
                   contact_local_time_to: Optional[int] = None,
                   message_opens: Optional[int] = None,
                   page_size: Optional[int] = None,
                   page_after: Optional[str] = None,
                   page_before: Optional[str] = None) -> Any:
        """GET /tasks — one user's tasks (calls, LinkedIn steps, manual emails…)."""
        if not user_id:
            raise ValueError("user_id is required")
        return self._request("GET", "/tasks", params={
            "user_id": user_id, "status": status, "type": type,
            "automatic": automatic, "sequence_key": sequence_key,
            "keyword": keyword, "contact_local_time_from": contact_local_time_from,
            "contact_local_time_to": contact_local_time_to,
            "message_opens": message_opens,
            **page_params(page_size, page_after, page_before)})

    def list_task_statuses(self) -> Any:
        """GET /tasks/statuses — the task statuses."""
        return self._request("GET", "/tasks/statuses")

    def list_task_types(self) -> Any:
        """GET /tasks/types — the task types."""
        return self._request("GET", "/tasks/types")

    def complete_task(self, task_id: str,
                      set_lead_to_completed: Optional[bool] = None) -> Any:
        """POST /tasks/{id}/complete — mark a task done; the sequence moves the
        lead on (or completes it with `set_lead_to_completed`)."""
        return self._request(
            "POST", f"/tasks/{path_id('task_id', task_id)}/complete",
            body={"set_lead_to_completed": set_lead_to_completed})

    def skip_task(self, task_id: str) -> Any:
        """POST /tasks/{id}/skip — skip a task; the sequence moves the lead on."""
        return self._request("POST", f"/tasks/{path_id('task_id', task_id)}/skip")

    # --- calls ------------------------------------------------------------------

    def list_calls(self, user_id: Optional[str] = None, from_number: Optional[str] = None,
                   to_number: Optional[str] = None, direction: Optional[str] = None,
                   start_date_from: Optional[str] = None,
                   start_date_to: Optional[str] = None,
                   page_size: Optional[int] = None,
                   page_after: Optional[str] = None,
                   page_before: Optional[str] = None) -> Any:
        """GET /calls — calls, newest first (calls that never connected are
        not returned). Dates are ISO 8601; `direction`: incoming, outgoing."""
        return self._request("GET", "/calls", params={
            "user_id": user_id, "from": from_number, "to": to_number,
            "direction": direction, "start_date_from": start_date_from,
            "start_date_to": start_date_to,
            **page_params(page_size, page_after, page_before)})

    def list_call_dispositions(self) -> Any:
        """GET /calls/dispositions — the call outcomes a logged call can take."""
        return self._request("GET", "/calls/dispositions")

    def log_call(self, task_id: str, user_id: str, from_number: str, to_number: str,
                 duration: int, answered: bool, human: bool,
                 disposition_id: Optional[str] = None,
                 transcription: Optional[str] = None,
                 recording_url: Optional[str] = None) -> Any:
        """POST /calls — log a call made outside Amplemarket against a task. A
        disposition whose action is "complete" completes the task."""
        return self._request("POST", "/calls", body={
            "task_id": task_id, "user_id": user_id, "from": from_number,
            "to": to_number, "duration": duration, "answered": answered,
            "human": human, "disposition_id": disposition_id,
            "transcription": transcription, "recording_url": recording_url})

    # --- exclusion lists --------------------------------------------------------

    def list_excluded_emails(self, email: Optional[str] = None,
                             page_size: Optional[int] = None,
                             page_after: Optional[str] = None,
                             page_before: Optional[str] = None) -> Any:
        """GET /excluded-emails — the emails never contacted (`email` filters
        by keyword)."""
        return self._request("GET", "/excluded-emails", params={
            "filter[email]": email, **page_params(page_size, page_after, page_before)})

    def list_excluded_domains(self, domain: Optional[str] = None,
                              page_size: Optional[int] = None,
                              page_after: Optional[str] = None,
                              page_before: Optional[str] = None) -> Any:
        """GET /excluded-domains — the domains never contacted."""
        return self._request("GET", "/excluded-domains", params={
            "filter[domain]": domain, **page_params(page_size, page_after, page_before)})

    def add_excluded_emails(self, emails: List[Any]) -> Any:
        """POST /excluded-emails — exclude emails from all outreach."""
        return self._request("POST", "/excluded-emails", body={
            "excluded_emails": items("emails", emails, "email")})

    def add_excluded_domains(self, domains: List[Any]) -> Any:
        """POST /excluded-domains — exclude domains from all outreach."""
        return self._request("POST", "/excluded-domains", body={
            "excluded_domains": items("domains", domains, "domain")})

    def remove_excluded_emails(self, emails: List[Any]) -> Any:
        """DELETE /excluded-emails — lift the exclusion of emails: they can be
        contacted again."""
        return self._request("DELETE", "/excluded-emails", body={
            "excluded_emails": items("emails", emails, "email")})

    def remove_excluded_domains(self, domains: List[Any]) -> Any:
        """DELETE /excluded-domains — lift the exclusion of domains."""
        return self._request("DELETE", "/excluded-domains", body={
            "excluded_domains": items("domains", domains, "domain")})

    # --- phone numbers ----------------------------------------------------------

    def review_phone_number(self, phone_number_id: str, user_id: str,
                            reason: str = "wrong_number") -> Any:
        """POST /phone_numbers/{id}/review — report a phone number as wrong."""
        return self._request(
            "POST", f"/phone_numbers/{path_id('phone_number_id', phone_number_id)}/review",
            body={"user_id": user_id, "reason": reason})
