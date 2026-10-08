"""Payslips (bulletins): their PDF image ids, their indices, and their computed content.

`v1/InfosSalaries/SalariesBulletins`, `v1/InfosBulletins/*`.

A payslip is addressed by (matricule, `identifiantEmploi`, month, `indicePeriode`): an
employee can hold several jobs in a month (intermittent contracts), and one job several
payslips in a month (supplementary, profit-sharing…), numbered by `indicePeriode` — 0
is the first one generated, the documented default. `bulletins_indices` lists them.
The selectors are keyword-only: two strings of the same type side by side are swapped
without any type error.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from ..periodes import MAX_MOIS, periode_datetime, plage

#: Line filters (snake_case accepted here → Silae field). Strings accept `%` as a
#: wildcard (`"SS0%"`, `"%heure%"`).
FILTRES_LIGNES = {
    "code_libelle": "codeLibelle",
    "libelle": "libelle",
    "code_ducs": "codeDucs",
    "zone": "zone",
    "marque_interne": "marqueInterne",
    "marque1": "marque1",
    "marque2": "marque2",
    "marque_dt_deb": "marqueDtDeb",
    "marque_dt_fin": "marqueDtFin",
    "compte4": "compte4",
    "compte6": "compte6",
    "exclure_lignes_neutres": "exclureLignesNeutres",
    "exclure_lignes_non_neutres": "exclureLignesNonNeutres",
}
#: Payslip zones: 2 = pay (base → gross), 3 = contributions (gross → total
#: deductions), 4 = net (taxable net → net paid).
ZONES = (2, 3, 4)
#: `typeDetails` of `SalarieBulletinDetails`: 1 header, 2 lines, 3 both.
TYPES_DETAILS = (1, 2, 3)


def _matricule(valeur: Optional[str]) -> str:
    if valeur is None or not str(valeur).strip():
        raise ValueError("matricule_salarie is required for a single employee's payslip")
    return str(valeur)


def _requete_bulletin(matricule_salarie: str, identifiant_emploi: int, periode: str,
                      indice_periode: Optional[int]) -> dict:
    requete = {
        "matriculeSalarie": _matricule(matricule_salarie),
        "identifiantEmploi": int(identifiant_emploi),
        "periode": periode_datetime(periode),
    }
    if indice_periode is not None:
        requete["indicePeriode"] = int(indice_periode)
    return requete


def filtres_lignes(filtres: Mapping[str, Any]) -> dict:
    """Silae's `RequeteSalarieBulletinLignesFiltres` from snake_case filters, validated:
    an unknown key or zone raises instead of being silently dropped."""
    inconnus = sorted(set(filtres) - set(FILTRES_LIGNES))
    if inconnus:
        raise ValueError(f"unknown line filter(s) {inconnus}; known: {sorted(FILTRES_LIGNES)}")
    out: dict = {}
    for cle, valeur in filtres.items():
        if valeur is None:
            continue
        if cle == "zone" and valeur not in ZONES:
            raise ValueError("zone: 2 (pay), 3 (contributions) or 4 (net)")
        if cle in ("marque_dt_deb", "marque_dt_fin"):
            valeur = periode_datetime(valeur, cle)
        out[FILTRES_LIGNES[cle]] = valeur
    return out


class _BulletinsMixin:

    def bulletins_ids_pdf(
        self,
        numero_dossier: str,
        *,
        periode_debut: str,
        periode_fin: str,
        matricule_salarie: str = "",
        identifiant_emploi: int = 0,
        originaux_seulement: Optional[bool] = None,
        etablissement: Optional[str] = None,
    ) -> Any:
        """Per employee and month, the ids of the payslip PDF IMAGES
        (`arr_ID_PAIBULLETIN`) and whether a Pôle Emploi certificate is attached — not
        the payslip content.

        Args:
            matricule_salarie: one employee; empty = every employee of the dossier.
            identifiant_emploi: one job; 0 = every job.
            originaux_seulement: only original payslips (True) or not (False).
            etablissement: keep one establishment (internal name).
        """
        debut, fin = plage(periode_debut, periode_fin)
        requete: dict = {"matriculeSalarie": matricule_salarie or "",
                         "identifiantEmploi": int(identifiant_emploi),
                         "periodeDebut": debut, "periodeFin": fin}
        if originaux_seulement is not None:
            requete["bulletinsOriginauxSeulement"] = bool(originaux_seulement)
        body: dict = {"requeteSalariesBulletins": requete, "numeroDossier": numero_dossier}
        if etablissement is not None:
            body["nomInterneEtablissement"] = etablissement
        return self.call("v1/InfosSalaries/SalariesBulletins", body,
                         numero_dossier=numero_dossier)

    def bulletins_indices(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        identifiant_emploi: int,
        periode_debut: str,
        periode_fin: str,
        originaux_seulement: Optional[bool] = None,
    ) -> Any:
        """Per month, the `indicePeriode` and label of each payslip of one job."""
        debut, fin = plage(periode_debut, periode_fin)
        requete: dict = {"matriculeSalarie": _matricule(matricule_salarie),
                         "identifiantEmploi": int(identifiant_emploi),
                         "periodeDebut": debut, "periodeFin": fin}
        if originaux_seulement is not None:
            requete["bulletinsOriginauxSeulement"] = bool(originaux_seulement)
        # Silae names this envelope `requeteSalariesBulletins` here too.
        return self.call("v1/InfosBulletins/SalarieBulletinsIndices",
                         {"requeteSalariesBulletins": requete, "numeroDossier": numero_dossier},
                         numero_dossier=numero_dossier)

    def bulletin_entete(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        identifiant_emploi: int,
        periode: str,
        indice_periode: Optional[int] = None,
    ) -> Any:
        """Payslip header: gross, net paid, taxable net, deduction totals (employee
        deductible / non-deductible, employer), hours, contract code."""
        return self.call(
            "v1/InfosBulletins/SalarieBulletinEntete",
            {"requeteSalarieBulletinEntete": _requete_bulletin(
                matricule_salarie, identifiant_emploi, periode, indice_periode),
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def bulletin_lignes(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        identifiant_emploi: int,
        periode: str,
        indice_periode: Optional[int] = None,
    ) -> Any:
        """Every payslip line (totals excluded): code, label, employee and employer
        base / rate / amount, DUCS code, marks."""
        return self.call(
            "v1/InfosBulletins/SalarieBulletinLignes",
            {"requeteSalarieBulletinLignes": _requete_bulletin(
                matricule_salarie, identifiant_emploi, periode, indice_periode),
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def bulletin_lignes_filtrees(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        identifiant_emploi: int,
        periode: str,
        filtres: Mapping[str, Any],
        indice_periode: Optional[int] = None,
    ) -> Any:
        """The payslip lines matching `filtres` (keys of `FILTRES_LIGNES`)."""
        f = filtres_lignes(filtres)
        if not f:
            raise ValueError("filtres: at least one filter is required (or use bulletin_lignes)")
        return self.call(
            "v1/InfosBulletins/SalarieBulletinLignesSelonFiltres",
            {"requeteSalarieBulletinLignes": _requete_bulletin(
                matricule_salarie, identifiant_emploi, periode, indice_periode),
             "requeteSalarieBulletinLignesFiltres": f,
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def bulletin_details(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        identifiant_emploi: int,
        periode: str,
        type_details: int = 3,
        filtres: Optional[Mapping[str, Any]] = None,
        indice_periode: Optional[int] = None,
    ) -> Any:
        """Header and/or lines of the payslip(s), as an array of `bulletins`.

        Args:
            type_details: 1 header only, 2 lines only, 3 both.
            filtres: optional line filters (keys of `FILTRES_LIGNES`); none = all lines.
        """
        if type_details not in TYPES_DETAILS:
            raise ValueError("type_details: 1 (header), 2 (lines) or 3 (both)")
        requete = {"typeDetails": type_details,
                   **_requete_bulletin(matricule_salarie, identifiant_emploi, periode,
                                       indice_periode)}
        body: dict = {"requeteSalarieBulletinDetails": requete, "numeroDossier": numero_dossier}
        f = filtres_lignes(filtres or {})
        if f:
            body["requeteSalarieBulletinFiltres"] = f
        return self.call("v1/InfosBulletins/SalarieBulletinDetails", body,
                         numero_dossier=numero_dossier)

    def bulletin_cumuls(
        self,
        numero_dossier: str,
        matricule_salarie: str,
        *,
        periode_debut: str,
        periode_fin: str,
    ) -> Any:
        """One employee's cumulative totals over a range of 12 months at most: base
        salary, gross, deductions, taxable net, net paid, withholding tax, social
        security ceiling, hours and days worked."""
        debut, fin = plage(periode_debut, periode_fin, max_mois=MAX_MOIS)
        return self.call(
            "v1/InfosBulletins/SalarieBulletinCumuls",
            {"periodeDebut": debut, "periodeFin": fin,
             "matriculeSalarie": _matricule(matricule_salarie),
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )
