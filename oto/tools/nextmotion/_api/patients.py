"""Nextmotion — the clinic's patient list, for AGGREGATES only.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_list`).

The rows carry a person (name, contact details, birth date, gender, address,
doctor comments). This method exists so a caller can COUNT the clientele by
zip code, age band or gender; serving the rows themselves is the caller's
decision, and the oto backend never does.

⚠️ The filters that match on a person are deliberately absent: `search` (name,
birth date, phone, id), `phone_number`, `birth_date` — any of them would let a
caller look a person up. So is `invoice_total__gt`: it would pair spending with
a list that names people.
"""
from __future__ import annotations

from typing import Any, Optional

from .._http import _id


class _PatientsMixin:

    def list_patients(self, clinic_id: str, *, is_archived: Optional[bool] = None,
                      limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/patients.

        Args:
            is_archived: True = archived only, False = active only, omitted = all.
        """
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/patients",
                          limit, offset, is_archived=is_archived)
