"""Payroll reports (éditions) and declaration summaries — generated documents.

`v1/EditionEtatsPaie/EditionDetailDesCotisations*`, `…/EditionTableauDesCharges*`,
`v1/DeclarationPDF/RecupererDeclarations*`.

Each function exists three ways: synchronous (the document in the response),
`…Asynchrone` (a task id, `guidTache`) and `Statut…Asynchrone` (a GET that polls the
task: `statut` ETAT_ENCOURS / ETAT_TERMINEE / ETAT_ERREUR, then the document). A
document comes back as `{"data": bytes, "filename", "mimetype"}`.
"""
from __future__ import annotations

from typing import Any, Optional

from ...common import UpstreamHTTPError
from ..periodes import MAX_MOIS, mois, periode_date, plage

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: Requested format → (Silae `format` value, MIME type, extension).
FORMATS = {"pdf": ("Pdf", "application/pdf", "pdf"), "xlsx": ("ExcelXLSX", XLSX_MIME, "xlsx")}
#: `grouperPar` of the contributions report.
GROUPEMENTS = {"sans_groupement": "SansGroupement", "mensuel": "Mensuel",
               "trimestriel": "Trimestriel"}
#: The report families: Silae function stem and file name stem.
EDITIONS = {
    "detail_cotisations": ("EditionEtatsPaie/EditionDetailDesCotisations",
                           "detail-des-cotisations"),
    "tableau_charges": ("EditionEtatsPaie/EditionTableauDesCharges", "tableau-des-charges"),
    "recap_declarations": ("DeclarationPDF/RecupererDeclarations", "recap-declarations"),
}


def _format(fmt: str) -> tuple[str, str, str]:
    if fmt not in FORMATS:
        raise ValueError(f"format: one of {sorted(FORMATS)}, got {fmt!r}")
    return FORMATS[fmt]


def _reconnaitre(data: bytes) -> tuple[str, str]:
    """(MIME, extension) of a document read back from a task, whose status does not
    say which format was asked."""
    if data.startswith(b"%PDF"):
        return "application/pdf", "pdf"
    if data.startswith(b"PK\x03\x04"):
        return XLSX_MIME, "xlsx"
    return "application/octet-stream", "bin"


def _mois_txt(valeur: str, champ: str) -> str:
    an, mo = mois(valeur, champ)
    return f"{an:04d}-{mo:02d}"


class _EditionsMixin:

    # --- request bodies ---

    @staticmethod
    def _corps_detail_cotisations(
        numero_dossier: str, periode_debut: str, periode_fin: str, fmt: str,
        detail_salaries: Optional[bool], grouper_par_organismes: Optional[bool],
        grouper_en_ignorant_codes_ducs: Optional[bool], grouper_par: Optional[str],
        detail_par_nature_de_cotisation: Optional[bool], axe_analytique: Optional[str],
    ) -> dict:
        debut, fin = plage(periode_debut, periode_fin, max_mois=MAX_MOIS)
        body: dict = {"format": _format(fmt)[0], "periodeDebut": debut, "periodeFin": fin,
                      "numeroDossier": numero_dossier}
        if grouper_par is not None:
            if grouper_par not in GROUPEMENTS:
                raise ValueError(f"grouper_par: one of {sorted(GROUPEMENTS)}")
            body["grouperPar"] = GROUPEMENTS[grouper_par]
        for cle, valeur in (("detailSalaries", detail_salaries),
                            ("grouperParOrganismes", grouper_par_organismes),
                            ("grouperEnIgnorantLesCodesDucs", grouper_en_ignorant_codes_ducs),
                            ("detailParNatureDeCotisation", detail_par_nature_de_cotisation),
                            ("axeAnalytique", axe_analytique)):
            if valeur is not None:
                body[cle] = valeur
        return body

    @staticmethod
    def _corps_tableau_charges(numero_dossier: str, periode_debut: str, periode_fin: str,
                               fmt: str) -> dict:
        debut, fin = plage(periode_debut, periode_fin)
        return {"format": _format(fmt)[0], "periodeDebut": debut, "periodeFin": fin,
                "numeroDossier": numero_dossier}

    # --- generic: synchronous, start, status ---

    def _generer(self, edition: str, body: dict, numero_dossier: str, filename: str,
                 mimetype: str) -> dict:
        fonction, _ = EDITIONS[edition]
        rendu = self.call(f"v1/{fonction}", body, numero_dossier=numero_dossier, timeout=180)
        return self._document(rendu, "document", filename, mimetype)

    def _lancer(self, edition: str, body: dict, numero_dossier: str) -> Any:
        fonction, _ = EDITIONS[edition]
        return self.call(f"v1/{fonction}Asynchrone", body, numero_dossier=numero_dossier)

    def statut_edition(self, edition: str, guid_tache: str,
                       numero_dossier: Optional[str] = None) -> dict:
        """Poll an asynchronous report task. Returns `statut`, `progression`,
        `messageErreur`, `dureeExecution` and `document` — the decoded document once
        the task is ETAT_TERMINEE, else None.

        Args:
            edition: "detail_cotisations", "tableau_charges" or "recap_declarations".
            guid_tache: the `guidTache` returned when the task was started.
        """
        if edition not in EDITIONS:
            raise ValueError(f"edition: one of {sorted(EDITIONS)}")
        if not guid_tache:
            raise ValueError("guid_tache is required")
        fonction, nom = EDITIONS[edition]
        famille, base = fonction.split("/")
        rendu = self.call(f"v1/{famille}/Statut{base}Asynchrone", method="GET",
                          params={"guidTache": guid_tache}, numero_dossier=numero_dossier)
        if not isinstance(rendu, dict):
            raise UpstreamHTTPError(502, {"error": "status response is not an object"},
                                    service="silae")
        out = {k: rendu.get(k) for k in ("statut", "progression", "messageErreur",
                                         "dureeExecution")}
        out["document"] = None
        if rendu.get("document"):
            doc = self._document(rendu, "document", nom, "application/octet-stream")
            mime, ext = _reconnaitre(doc["data"])
            out["document"] = {**doc, "filename": f"silae-{nom}-{guid_tache[:8]}.{ext}",
                               "mimetype": mime}
        return out

    # --- Détail des cotisations ---

    def edition_detail_cotisations(
        self, numero_dossier: str, *, periode_debut: str, periode_fin: str,
        format: str = "pdf", detail_salaries: Optional[bool] = None,
        grouper_par_organismes: Optional[bool] = None,
        grouper_en_ignorant_codes_ducs: Optional[bool] = None,
        grouper_par: Optional[str] = None,
        detail_par_nature_de_cotisation: Optional[bool] = None,
        axe_analytique: Optional[str] = None,
    ) -> dict:
        """The contributions detail report over 12 months at most, as a document.

        Booleans are passed as Silae documents them (unset = Silae's default);
        `detail_par_nature_de_cotisation=True` means NO detail by nature, per Silae's
        own description. `grouper_par`: sans_groupement | mensuel | trimestriel.
        """
        body = self._corps_detail_cotisations(
            numero_dossier, periode_debut, periode_fin, format, detail_salaries,
            grouper_par_organismes, grouper_en_ignorant_codes_ducs, grouper_par,
            detail_par_nature_de_cotisation, axe_analytique)
        _, mime, ext = _format(format)
        nom = (f"silae-detail-des-cotisations-{numero_dossier}-"
               f"{_mois_txt(periode_debut, 'periode_debut')}_"
               f"{_mois_txt(periode_fin, 'periode_fin')}.{ext}")
        return self._generer("detail_cotisations", body, numero_dossier, nom, mime)

    def lancer_edition_detail_cotisations(
        self, numero_dossier: str, *, periode_debut: str, periode_fin: str,
        format: str = "pdf", detail_salaries: Optional[bool] = None,
        grouper_par_organismes: Optional[bool] = None,
        grouper_en_ignorant_codes_ducs: Optional[bool] = None,
        grouper_par: Optional[str] = None,
        detail_par_nature_de_cotisation: Optional[bool] = None,
        axe_analytique: Optional[str] = None,
    ) -> Any:
        """Start the same report asynchronously → `{"guidTache"}`."""
        body = self._corps_detail_cotisations(
            numero_dossier, periode_debut, periode_fin, format, detail_salaries,
            grouper_par_organismes, grouper_en_ignorant_codes_ducs, grouper_par,
            detail_par_nature_de_cotisation, axe_analytique)
        return self._lancer("detail_cotisations", body, numero_dossier)

    # --- Tableau des charges ---

    def edition_tableau_charges(self, numero_dossier: str, *, periode_debut: str,
                                periode_fin: str, format: str = "pdf") -> dict:
        """The charges table report (Silae documents no range limit), as a document."""
        body = self._corps_tableau_charges(numero_dossier, periode_debut, periode_fin, format)
        _, mime, ext = _format(format)
        nom = (f"silae-tableau-des-charges-{numero_dossier}-"
               f"{_mois_txt(periode_debut, 'periode_debut')}_"
               f"{_mois_txt(periode_fin, 'periode_fin')}.{ext}")
        return self._generer("tableau_charges", body, numero_dossier, nom, mime)

    def lancer_edition_tableau_charges(self, numero_dossier: str, *, periode_debut: str,
                                       periode_fin: str, format: str = "pdf") -> Any:
        """Start the same report asynchronously → `{"guidTache"}`."""
        body = self._corps_tableau_charges(numero_dossier, periode_debut, periode_fin, format)
        return self._lancer("tableau_charges", body, numero_dossier)

    # --- Récapitulatifs des déclarations (PDF) ---

    def recap_declarations(self, numero_dossier: str, *, periode: str) -> dict:
        """The summaries of the month's declarations, merged into one PDF."""
        nom = f"silae-recap-declarations-{numero_dossier}-{_mois_txt(periode, 'periode')}.pdf"
        return self._generer("recap_declarations",
                             {"periode": periode_date(periode), "numeroDossier": numero_dossier},
                             numero_dossier, nom, "application/pdf")

    def lancer_recap_declarations(self, numero_dossier: str, *, periode: str) -> Any:
        """Start the same retrieval asynchronously → `{"guidTache"}`."""
        return self._lancer("recap_declarations",
                            {"periode": periode_date(periode), "numeroDossier": numero_dossier},
                            numero_dossier)
