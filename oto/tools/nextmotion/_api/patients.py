"""Nextmotion — the patient's IDENTITY: the clinic's patient list (with its
look-up filters), one patient's record, creating and updating one.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`, `_post`, `_put`).

A patient record carries a person (name, contact details, birth date, gender,
address, contact consents) and also the practitioner's comments and a
photograph. This client returns responses as the API sends them; reducing them
is the caller's decision. The medical file around the patient (history,
photos and media, prescriptions, treatments, consultations, visits) has no
method, nor has deleting a patient.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .._http import _body, _id


class _PatientsMixin:

    def list_patients(self, clinic_id: str, *, search: Optional[str] = None,
                      birth_date: Optional[str] = None,
                      phone_number: Optional[str] = None,
                      invoice_total_gt: Optional[str] = None,
                      is_archived: Optional[bool] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/patients.

        Args:
            search: matches (part of) the name, the birth date, the phone number
                or the id.
            birth_date: `YYYY-MM-DD` or `MM-DD`.
            phone_number: that phone number.
            invoice_total_gt: invoiced total above this amount, e.g. `100.00`
                (sent as `invoice_total__gt`).
            is_archived: True = archived only, False = active only, omitted = all.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/patients",
                          limit, offset, search=search, birth_date=birth_date,
                          phone_number=phone_number, invoice_total__gt=invoice_total_gt,
                          is_archived=is_archived)

    def get_patient(self, patient_id: str) -> Any:
        """GET /v4/patients/{patient_id}."""
        return self._get(f"/v4/patients/{_id(patient_id, 'patient_id')}")

    def create_patient(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/patients (`email`, `first_name`,
        `last_name`, `gender` required)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/patients",
                          _body(body))

    def update_patient(self, patient_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/patients/{patient_id} — same fields as `create_patient`."""
        return self._put(f"/v4/patients/{_id(patient_id, 'patient_id')}", _body(body))
