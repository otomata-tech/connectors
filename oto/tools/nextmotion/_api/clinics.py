"""Nextmotion — the key's user, clinics, subscription features, practitioners.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`, `_post`, `_put`, `_delete`).
"""
from __future__ import annotations

from typing import Any, Dict

from .._http import _body, _id


class _ClinicsMixin:

    def get_me(self) -> Any:
        """GET /v4/users/me — the user the key acts as."""
        return self._get("/v4/users/me")

    def list_clinics(self, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics — the clinics this user belongs to."""
        return self._list("/v4/clinics", limit, offset)

    def list_clinic_features(self, clinic_id: str, *, limit: int = 50,
                             offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/features — the clinic's Nextmotion
        subscription options (code, prices, counts, sale/cancel/end times)."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/features",
                          limit, offset)

    def list_doctors(self, clinic_id: str, *, limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/doctors — the clinic's practitioners."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/doctors",
                          limit, offset)

    def get_doctor(self, doctor_id: str) -> Any:
        """GET /v4/doctors/{doctor_id}."""
        return self._get(f"/v4/doctors/{_id(doctor_id, 'doctor_id')}")

    def create_doctor(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/doctors — adds a collaborator (`email` and
        `kind` required; names required when the email is not a known user)."""
        return self._post(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/doctors", _body(body))

    def update_doctor(self, doctor_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/doctors/{doctor_id} (`speciality` required)."""
        return self._put(f"/v4/doctors/{_id(doctor_id, 'doctor_id')}", _body(body))

    def delete_doctor(self, doctor_id: str) -> Any:
        """DELETE /v4/doctors/{doctor_id} — answers 204."""
        return self._delete(f"/v4/doctors/{_id(doctor_id, 'doctor_id')}")
