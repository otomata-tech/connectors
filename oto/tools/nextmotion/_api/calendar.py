"""Nextmotion — the calendar: appointments, free slots, online appointment
requests, patient journeys, absences, opening hours, rooms and devices.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_request`, `_get`, `_list`, `_post`, `_put`, `_delete`).

⚠️ Two filters of the API are deliberately absent: `search` on journeys matches
the patient's NAME, and `order=patient_name` sorts by it — either would let a
caller pair a person with a visit. Filter by patient id instead.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._http import _body, _clean, _id, _ids, _opt_id


class _CalendarMixin:

    # ---- appointments -------------------------------------------------------

    def list_appointments(self, clinic_id: str, *, date: Optional[str] = None,
                          patient_id: Optional[str] = None,
                          limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/calendar_appointments.

        Args:
            date: `YYYY-MM-DD` — appointments on that day.
            patient_id: appointments of that patient (sent as `patient`).
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_appointments",
            limit, offset, date=date, patient=_opt_id(patient_id, "patient_id"))

    def get_appointment(self, appointment_id: str) -> Any:
        """GET /v4/calendar_appointments/{calendar_appointment_id}."""
        return self._get(
            f"/v4/calendar_appointments/{_id(appointment_id, 'appointment_id')}")

    def reschedule_appointment(self, appointment_id: str, *,
                               visit_type_opening_hour_id: str,
                               time_slot: str) -> Any:
        """POST /v4/calendar_appointments/{id}/reschedule — moves the appointment.

        Both body fields are required by the spec and come from
        `search_time_slots`: the slot's `id` and its `time_slot` (date-time).
        """
        if not time_slot:
            raise ValueError("time_slot est requis.")
        body = {
            "visit_type_opening_hour": _id(visit_type_opening_hour_id,
                                           "visit_type_opening_hour_id"),
            "time_slot": time_slot,
        }
        return self._request(
            "POST",
            f"/v4/calendar_appointments/{_id(appointment_id, 'appointment_id')}/reschedule",
            json_body=body)

    def update_appointment(self, appointment_id: str, *, body: Dict[str, Any]) -> Any:
        """PUT /v4/calendar_appointments/{id} (`calendar_event` required, with its
        `start_time` and `end_time`)."""
        return self._put(
            f"/v4/calendar_appointments/{_id(appointment_id, 'appointment_id')}",
            _body(body))

    def delete_appointment(self, appointment_id: str) -> Any:
        """DELETE /v4/calendar_appointments/{id} — answers 204."""
        return self._request(
            "DELETE", f"/v4/calendar_appointments/{_id(appointment_id, 'appointment_id')}")

    def search_time_slots(self, clinic_id: str, *, start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          sub_visit_type_id: Optional[str] = None,
                          sub_visit_type_name: Optional[str] = None,
                          doctor_id: Optional[str] = None,
                          doctor_name: Optional[str] = None) -> Any:
        """POST /v4/clinics/{clinic_id}/visit_types/opening_hours — free slots.

        A read despite the verb. Dates are `YYYY-MM-DD`; omitted, the search
        spans the current month. Answers `{data: [{id, type, time_slot,
        utc_offset}]}`, unpaginated.
        """
        body = _clean({
            "start_date": start_date, "end_date": end_date,
            "sub_visit_type_id": _opt_id(sub_visit_type_id, "sub_visit_type_id"),
            "sub_visit_type_name": sub_visit_type_name,
            "doctor_id": _opt_id(doctor_id, "doctor_id"), "doctor_name": doctor_name,
        })
        return self._request(
            "POST", f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/visit_types/opening_hours",
            json_body=body)

    # ---- online appointment requests & journeys ----------------------------

    def list_appointment_requests(self, clinic_id: str, *, status: Optional[str] = None,
                                  limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/appointment_requests.

        Args:
            status: `new` | `pending_pre_payment` | `accepted` | `rejected`.
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/appointment_requests",
            limit, offset, status=status)

    def get_appointment_request(self, appointment_request_id: str) -> Any:
        """GET /v4/appointment_requests/{appointment_request_id}."""
        return self._get("/v4/appointment_requests/"
                         f"{_id(appointment_request_id, 'appointment_request_id')}")

    def create_appointment_request(self, *, body: Dict[str, Any]) -> Any:
        """POST /v4/appointment_requests — an online booking request on a free slot
        (`visit_type_opening_hour`, `time_slot` from `search_time_slots`) for a
        person (`first_name`, `last_name`, `email`, `phone_number`, `birth_date`).
        The clinic is the slot's: the path carries none."""
        return self._post("/v4/appointment_requests", _body(body))

    def list_calendar_journeys(self, clinic_id: str, *, start_date: Optional[str] = None,
                               end_date: Optional[str] = None,
                               include_ongoing: Optional[bool] = None,
                               status: Optional[str] = None,
                               doctor_ids: Optional[List[str]] = None,
                               visit_type_ids: Optional[List[str]] = None,
                               sub_visit_type_ids: Optional[List[str]] = None,
                               patient_id: Optional[str] = None,
                               order: Optional[str] = None,
                               limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/calendar_journeys — a patient's path
        through the clinic on an appointment (required / completed steps).

        Args:
            start_date / end_date: ISO 8601 date or date-time.
            include_ongoing: include events overlapping a bound (API default true).
            status: `not_started` | `in_progress` | `finished`.
            doctor_ids / visit_type_ids / sub_visit_type_ids: UUID lists.
            patient_id: that patient's journeys (sent as `patient`).
            order: `start_time` | `-start_time` (sorting by patient name is not
                exposed).
        """
        if order is not None and order not in ("start_time", "-start_time"):
            raise ValueError(f"order doit être 'start_time' ou '-start_time' — reçu {order!r}.")
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_journeys",
            limit, offset, start_date=start_date, end_date=end_date,
            include_ongoing=include_ongoing, status=status,
            doctor=_ids(doctor_ids, "doctor_ids"),
            visit_type=_ids(visit_type_ids, "visit_type_ids"),
            sub_visit_type=_ids(sub_visit_type_ids, "sub_visit_type_ids"),
            patient=_opt_id(patient_id, "patient_id"), order=order)

    # ---- absences, opening hours, rooms, devices ----------------------------

    def list_calendar_absences(self, clinic_id: str, *, start_date: Optional[str] = None,
                               end_date: Optional[str] = None,
                               show_all: Optional[bool] = None,
                               limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/calendar_absences.

        Args:
            start_date / end_date: `YYYY-MM-DD`.
            show_all: every absence of the clinic, not only the key user's (API
                default false).
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_absences",
            limit, offset, start_date=start_date, end_date=end_date, show_all=show_all)

    def get_calendar_absence(self, calendar_absence_id: str) -> Any:
        """GET /v4/calendar_absences/{calendar_absence_id}."""
        return self._get("/v4/calendar_absences/"
                         f"{_id(calendar_absence_id, 'calendar_absence_id')}")

    def list_calendar_opening_hours(self, clinic_id: str, *,
                                    show_all: Optional[bool] = None,
                                    limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/calendar_opening_hours.

        Args:
            show_all: every opening hour of the clinic, not only the key user's
                (API default false).
        """
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_opening_hours",
            limit, offset, show_all=show_all)

    def get_calendar_opening_hour(self, calendar_opening_hour_id: str) -> Any:
        """GET /v4/calendar_opening_hours/{calendar_opening_hour_id}."""
        return self._get("/v4/calendar_opening_hours/"
                         f"{_id(calendar_opening_hour_id, 'calendar_opening_hour_id')}")

    def list_appointment_rooms(self, clinic_id: str, *, limit: int = 50,
                               offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/appointment_rooms."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/appointment_rooms", limit, offset)

    def get_appointment_room(self, appointment_room_id: str) -> Any:
        """GET /v4/appointment_rooms/{appointment_room_id}."""
        return self._get(
            f"/v4/appointment_rooms/{_id(appointment_room_id, 'appointment_room_id')}")

    def list_appointment_devices(self, clinic_id: str, *, limit: int = 50,
                                 offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/appointment_devices."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/appointment_devices",
            limit, offset)

    def get_appointment_device(self, appointment_device_id: str) -> Any:
        """GET /v4/appointment_devices/{appointment_device_id}."""
        return self._get("/v4/appointment_devices/"
                         f"{_id(appointment_device_id, 'appointment_device_id')}")

    # ---- writes: absences, opening hours, rooms, devices --------------------

    def create_calendar_absence(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/calendar_absences (`calendar_event` required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_absences", _body(body))

    def update_calendar_absence(self, calendar_absence_id: str, *,
                                body: Dict[str, Any]) -> Any:
        """PUT /v4/calendar_absences/{calendar_absence_id}."""
        return self._put("/v4/calendar_absences/"
                         f"{_id(calendar_absence_id, 'calendar_absence_id')}", _body(body))

    def delete_calendar_absence(self, calendar_absence_id: str) -> Any:
        """DELETE /v4/calendar_absences/{calendar_absence_id} — answers 204."""
        return self._delete("/v4/calendar_absences/"
                            f"{_id(calendar_absence_id, 'calendar_absence_id')}")

    def create_calendar_opening_hour(self, clinic_id: str, *,
                                     body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/calendar_opening_hours (`calendar_event`
        required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/calendar_opening_hours",
            _body(body))

    def update_calendar_opening_hour(self, calendar_opening_hour_id: str, *,
                                     body: Dict[str, Any]) -> Any:
        """PUT /v4/calendar_opening_hours/{calendar_opening_hour_id}."""
        return self._put(
            "/v4/calendar_opening_hours/"
            f"{_id(calendar_opening_hour_id, 'calendar_opening_hour_id')}", _body(body))

    def delete_calendar_opening_hour(self, calendar_opening_hour_id: str) -> Any:
        """DELETE /v4/calendar_opening_hours/{calendar_opening_hour_id} — answers 204."""
        return self._delete(
            "/v4/calendar_opening_hours/"
            f"{_id(calendar_opening_hour_id, 'calendar_opening_hour_id')}")

    def create_appointment_room(self, clinic_id: str, *, body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/appointment_rooms (`name` required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/appointment_rooms", _body(body))

    def update_appointment_room(self, appointment_room_id: str, *,
                                body: Dict[str, Any]) -> Any:
        """PUT /v4/appointment_rooms/{appointment_room_id} (`name` required)."""
        return self._put(
            f"/v4/appointment_rooms/{_id(appointment_room_id, 'appointment_room_id')}",
            _body(body))

    def delete_appointment_room(self, appointment_room_id: str) -> Any:
        """DELETE /v4/appointment_rooms/{appointment_room_id} — answers 204."""
        return self._delete(
            f"/v4/appointment_rooms/{_id(appointment_room_id, 'appointment_room_id')}")

    def create_appointment_device(self, clinic_id: str, *,
                                  body: Dict[str, Any]) -> Any:
        """POST /v4/clinics/{clinic_id}/appointment_devices (`name` required)."""
        return self._post(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/appointment_devices", _body(body))

    def update_appointment_device(self, appointment_device_id: str, *,
                                  body: Dict[str, Any]) -> Any:
        """PUT /v4/appointment_devices/{appointment_device_id} (`name` required)."""
        return self._put("/v4/appointment_devices/"
                         f"{_id(appointment_device_id, 'appointment_device_id')}",
                         _body(body))

    def delete_appointment_device(self, appointment_device_id: str) -> Any:
        """DELETE /v4/appointment_devices/{appointment_device_id} — answers 204."""
        return self._delete("/v4/appointment_devices/"
                            f"{_id(appointment_device_id, 'appointment_device_id')}")
