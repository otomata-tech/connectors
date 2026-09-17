"""Nextmotion — leads and the clinic's settings: object labels (lead sources,
statuses, quote tags…), communication and document templates, webhooks.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`).

⚠️ The API's `search` on leads matches a person's name, email or phone: it is
deliberately not exposed.
"""
from __future__ import annotations

from typing import Any, List, Optional

from .._http import _id, _opt_id


class _CrmMixin:

    def list_leads(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/leads — prospects (contact details, source,
        status, desired treatment, follow-up counters)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/leads",
                          limit, offset)

    def get_lead(self, lead_id: str) -> Any:
        """GET /v4/leads/{lead_id}."""
        return self._get(f"/v4/leads/{_id(lead_id, 'lead_id')}")

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

    def list_webhooks(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/webhooks."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/webhooks",
                          limit, offset)

    def get_webhook(self, webhook_id: str) -> Any:
        """GET /v4/webhooks/{webhook_id}."""
        return self._get(f"/v4/webhooks/{_id(webhook_id, 'webhook_id')}")
