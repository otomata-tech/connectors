"""Nextmotion — leads, the clinic's call and message log, and its settings: object
labels (lead sources, statuses, quote tags…), communication and document templates,
survey-form templates, webhooks.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`, `_post`, `_put`, `_delete`).

Survey forms are the clinic's TEMPLATES (BoltNote notes, treatment consents): their
name, type and fields. A patient's filled-in form is not reachable here.

⚠️ The API's `search` on leads matches a person's name, email or phone: it is
deliberately not exposed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._http import _body, _id, _opt_id


class _CrmMixin:

    def list_leads(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/leads — prospects (contact details, source,
        status, desired treatment, follow-up counters)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/leads",
                          limit, offset)

    def get_lead(self, lead_id: str) -> Any:
        """GET /v4/leads/{lead_id}."""
        return self._get(f"/v4/leads/{_id(lead_id, 'lead_id')}")

    def create_lead(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/leads (`first_name`, `last_name` required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/leads", _body(body))

    def update_lead(self, lead_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/leads/{lead_id} (`first_name`, `last_name` required)."""
        return self._put(f"/v4/leads/{_id(lead_id, 'lead_id')}", _body(body))

    def delete_lead(self, lead_id: str) -> Any:
        """DELETE /v4/leads/{lead_id} — answers 204."""
        return self._delete(f"/v4/leads/{_id(lead_id, 'lead_id')}")

    def convert_lead_to_patient(self, lead_id: str) -> Any:
        """POST /v4/leads/{lead_id}/convert_to_patient — answers the patient, an
        existing one when its details match."""
        return self._post(f"/v4/leads/{_id(lead_id, 'lead_id')}/convert_to_patient")

    def create_call(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/calls — logs a call (JSON; a recording file,
        multipart only, is not sent by this client)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calls", _body(body))

    def create_communication_record(self, clinic_id: str, *,
                                    body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/communication_records — SENDS an email, SMS
        or WhatsApp message built from one of the clinic's templates, about one
        object (`communication_template_kind`, `communication_template_type`,
        `object` required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/communication_records",
            _body(body))

    def list_object_labels(self, clinic_id: str, *, types: Optional[List[str]] = None,
                           limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/object_labels.

        Args:
            types: among `patient`, `call`, `quote_tag`, `quote_channel`,
                `lead_status`, `lead_source`, `lead_channel`,
                `lead_desired_treatment`, `lead_zone` (sent as repeated `type`).
        """
        if isinstance(types, str):
            raise ValueError(f"types doit être une liste — reçu {types!r}.")
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/object_labels",
                          limit, offset, type=types)

    def list_communication_templates(self, clinic_id: str, *, kind: Optional[str] = None,
                                     limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/communication_templates.

        Args:
            kind: `email` | `sms` | `whatsapp`.
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/communication_templates",
            limit, offset, kind=kind)

    def get_communication_template(self, communication_template_id: str) -> Any:
        """GET /v4/communication_templates/{communication_template_id}."""
        return self._get(
            "/v4/communication_templates/"
            f"{_id(communication_template_id, 'communication_template_id')}")

    def update_communication_template(self, communication_template_id: str, *,
                                      body: Dict[str, Any]) -> Any:
        """PUT /v4/communication_templates/{id} (`template` required)."""
        return self._put(
            "/v4/communication_templates/"
            f"{_id(communication_template_id, 'communication_template_id')}", _body(body))

    def list_communication_template_placeholders(self, *,
                                                 type: Optional[str] = None) -> Any:
        """GET /v4/communication_templates/autocomplete — the placeholders a
        template of that `type` (e.g. `quote`, `appointment_reminder`) can use."""
        return self._get("/v4/communication_templates/autocomplete", type=type)

    def list_document_templates(self, clinic_id: str, *, type: Optional[int] = None,
                                master_id: Optional[str] = None,
                                limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/document_templates.

        Args:
            type: 0 PRESCRIPTION, 1 QUOTE, 2 INVOICE, 3 DEPOSIT_INVOICE,
                4 CREDIT_NOTE, 6 IMAGE_RIGHTS_CONSENT, 7 ADMINISTRATIVE, 8 VISIT.
            master_id: templates linked to that master template (sent as `master`).
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/document_templates",
            limit, offset, type=type, master=_opt_id(master_id, "master_id"))

    def get_document_template(self, document_template_id: str) -> Any:
        """GET /v4/document_templates/{document_template_id}."""
        return self._get("/v4/document_templates/"
                         f"{_id(document_template_id, 'document_template_id')}")

    def create_document_template(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/document_templates (`name`, `template`,
        `type` required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/document_templates", _body(body))

    def update_document_template(self, document_template_id: str, *,
                                 body: Dict[str, Any]) -> Any:
        """PUT /v4/document_templates/{id} (`name`, `template` required)."""
        return self._put("/v4/document_templates/"
                         f"{_id(document_template_id, 'document_template_id')}",
                         _body(body))

    def delete_document_template(self, document_template_id: str) -> Any:
        """DELETE /v4/document_templates/{id} — answers 204."""
        return self._delete("/v4/document_templates/"
                            f"{_id(document_template_id, 'document_template_id')}")

    def duplicate_document_template(self, document_template_id: str) -> Any:
        """POST /v4/document_templates/{id}/duplicate — answers the copy."""
        return self._post("/v4/document_templates/"
                          f"{_id(document_template_id, 'document_template_id')}/duplicate")

    def list_document_template_placeholders(self, *, type: Optional[int] = None) -> Any:
        """GET /v4/document_templates/autocomplete — placeholders per document
        type (same codes as `list_document_templates`)."""
        return self._get("/v4/document_templates/autocomplete", type=type)

    # ---- survey-form templates ----------------------------------------------

    def list_survey_forms(self, clinic_id: str, *, search: Optional[str] = None,
                          type: Optional[str] = None,
                          limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/survey_forms.

        Args:
            search: matches the template's name.
            type: `bolt_note` | `treatment_consent`.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/survey_forms",
                          limit, offset, search=search, type=type)

    def get_survey_form(self, survey_form_id: str) -> Any:
        """GET /v4/survey_forms/{survey_form_id}."""
        return self._get(f"/v4/survey_forms/{_id(survey_form_id, 'survey_form_id')}")

    def create_survey_form(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/survey_forms (`type`, `name`, `fields_tmpl`
        required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/survey_forms",
                          _body(body))

    def update_survey_form(self, survey_form_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/survey_forms/{survey_form_id}."""
        return self._put(f"/v4/survey_forms/{_id(survey_form_id, 'survey_form_id')}",
                         _body(body))

    def delete_survey_form(self, survey_form_id: str) -> Any:
        """DELETE /v4/survey_forms/{survey_form_id} — answers 204."""
        return self._delete(f"/v4/survey_forms/{_id(survey_form_id, 'survey_form_id')}")

    def list_survey_form_placeholders(self, *, type: str) -> Any:
        """GET /v4/survey_forms/autocomplete — placeholders of a survey-form type
        (`bolt_note` | `treatment_consent`, required)."""
        if not type:
            raise ValueError("type est requis.")
        return self._get("/v4/survey_forms/autocomplete", type=type)

    # ---- webhooks -----------------------------------------------------------

    def list_webhooks(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/webhooks."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/webhooks",
                          limit, offset)

    def get_webhook(self, webhook_id: str) -> Any:
        """GET /v4/webhooks/{webhook_id}."""
        return self._get(f"/v4/webhooks/{_id(webhook_id, 'webhook_id')}")

    def create_webhook(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/webhooks (`action_type`, `url` required;
        `headers` usually carry the receiver's secret)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/webhooks",
                          _body(body))

    def update_webhook(self, webhook_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/webhooks/{webhook_id} (`url` required)."""
        return self._put(f"/v4/webhooks/{_id(webhook_id, 'webhook_id')}", _body(body))

    def delete_webhook(self, webhook_id: str) -> Any:
        """DELETE /v4/webhooks/{webhook_id} — answers 204."""
        return self._delete(f"/v4/webhooks/{_id(webhook_id, 'webhook_id')}")
