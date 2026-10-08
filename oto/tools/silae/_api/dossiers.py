"""Dossiers (payroll files): what the key reaches, and one dossier's settings.

`v1/InfosTechniquesDossiers/*`, `v1/FicheSociete/ListeEtablissementsDossierPaie`,
`v1/Organisme/ListeOrganismes`.
"""
from __future__ import annotations

from typing import Any, Optional


class _DossiersMixin:

    def list_dossiers(self) -> Any:
        """Dossiers reachable with the key: number, company name, SIRET, state…

        `typeDossiers: 0` lists every dossier, including those without an
        establishment (1 would keep only those with one). An empty body is refused by
        Silae (error 1001)."""
        return self.call("v1/InfosTechniquesDossiers/ListeDossiers", {"typeDossiers": 0})

    def list_numeros_dossiers(self) -> Any:
        """Only the numbers of the reachable dossiers (lighter than `list_dossiers`)."""
        return self.call("v1/InfosTechniquesDossiers/ListeNumerosDossiers", {})

    def list_conventions_collectives(self) -> Any:
        """The collective agreements configured in EVERY reachable dossier
        (`ListeInformationsDossiersPaie`: one entry per dossier, no filter)."""
        return self.call("v1/InfosTechniquesDossiers/ListeInformationsDossiersPaie", {})

    def dossier_periode_en_cours(self, numero_dossier: str) -> Any:
        """The open pay period of a dossier (`periodeEnCours`, first day of the month)."""
        return self.call(
            "v1/InfosTechniquesDossiers/DossierRecupererPeriodeEnCours",
            {"numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def list_etablissements(self, numero_dossier: str) -> Any:
        """The establishments of a dossier: internal name, SIRET, main or not."""
        return self.call(
            "v1/FicheSociete/ListeEtablissementsDossierPaie",
            {"numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def list_organismes(
        self,
        numero_dossier: str,
        code_organisme: Optional[str] = None,
        code_nature: Optional[str] = None,
        etablissement: Optional[str] = None,
    ) -> Any:
        """The social bodies of a dossier (URSSAF, pension, provident…) with their
        affiliation, periodicity and payment settings.

        Args:
            code_organisme: keep one body by its Silae code.
            code_nature: keep one nature (SSOC, CH, ARRCO, AGIRC, GRS, RPO, RPS, CCP,
                MT, FP, RSPECIAL, TS, ATCS, DIV).
            etablissement: keep the bodies of one establishment (internal name).
        """
        body: dict = {"numeroDossier": numero_dossier}
        for cle, valeur in (("codeOrganisme", code_organisme), ("codeNature", code_nature),
                            ("nomInterneEtablissement", etablissement)):
            if valeur is not None:
                body[cle] = valeur
        return self.call("v1/Organisme/ListeOrganismes", body, numero_dossier=numero_dossier)
