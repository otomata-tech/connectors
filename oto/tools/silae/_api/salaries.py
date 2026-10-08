"""Employees (salariés), their jobs (emplois) and the variables awaiting entry.

`v1/InfosSalaries/*`, `v1/SalarieEmplois/ListeSalarieEmplois`,
`v1/InfosTechniquesDossiers/MatriculeSalarie`, `v1/VariablesASaisir/*`.

An "emploi" is Silae's unit of a payslip: every pay-impacting change of the employee
record creates one, and the API always speaks of the EXTERNAL job id, stable across
those changes. `identifiantEmploi` comes from `list_salarie_emplois`.
"""
from __future__ import annotations

from typing import Any, Optional

from ..periodes import date_jour, periode_datetime


class _SalariesMixin:

    def list_salaries(
        self,
        numero_dossier: str,
        actif_sur_periode: Optional[str] = None,
        actif_a_la_date: Optional[str] = None,
    ) -> Any:
        """The employees of a dossier: matricule, displayed name, NIR, emails.

        Args:
            actif_sur_periode: keep those active during this pay month (`AAAA-MM`).
            actif_a_la_date: keep those active on this day (`AAAA-MM-JJ`). Both
                given = both criteria apply.
        """
        options: dict = {}
        if actif_sur_periode is not None:
            options["optionActifSurPeriode"] = periode_datetime(
                actif_sur_periode, "actif_sur_periode")
        if actif_a_la_date is not None:
            options["optionActifALaDate"] = date_jour(actif_a_la_date, "actif_a_la_date")
        body: dict = {"numeroDossier": numero_dossier}
        if options:
            body["listeSalariesOptions"] = options
        return self.call("v1/InfosSalaries/ListeSalaries", body, numero_dossier=numero_dossier)

    def lecture_informations_salarie(self, numero_dossier: str, matricule_salarie: str) -> Any:
        """The employee record: identity, NIR, birth, address, contacts, education,
        residence permit, and the latest job (current or archived)."""
        return self.call(
            "v1/InfosSalaries/LectureInformationsSalarie",
            {"matricule": matricule_salarie, "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def list_salarie_emplois(
        self, numero_dossier: str, matricule_salarie: str, type_emplois: int = 0
    ) -> Any:
        """An employee's jobs, each with its `identifiantEmploi`.

        Args:
            type_emplois: 0 = current jobs only, 1 = current and archived.
        """
        if type_emplois not in (0, 1):
            raise ValueError("type_emplois: 0 (current jobs) or 1 (current and archived)")
        return self.call(
            "v1/SalarieEmplois/ListeSalarieEmplois",
            {"typeEmplois": type_emplois, "matriculeSalarie": matricule_salarie,
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def matricule_depuis_interne(self, numero_dossier: str, matricule_interne: str) -> Any:
        """Translate an employee's INTERNAL matricule into their Silae matricule
        (`{"matriculeSalarie"}`) — nothing else about the employee."""
        return self.call(
            "v1/InfosTechniquesDossiers/MatriculeSalarie",
            {"matriculeInterneSalarie": matricule_interne, "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def list_variables_a_saisir(self, numero_dossier: str) -> Any:
        """The variable payroll elements (EVP) defined for entry in a dossier: name,
        family, labels, type, format. A definition list, not entered values."""
        return self.call(
            "v1/VariablesASaisir/ListeVariablesASaisir",
            {"numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )
