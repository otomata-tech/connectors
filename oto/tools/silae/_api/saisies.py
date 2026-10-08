"""WRITES into payroll entries: variable elements, bonuses, hours, confirmation.

`v1/ElementsVariables/*`, `v1/ActivitesEtHeures/SalarieAjouterHeures`. These change
what the next payslip computation will use; Silae reserves them to the "Usage interne
RH" contract (model 1B). An entry on a payslip already computed is refused by Silae
until that payslip is deleted.
"""
from __future__ import annotations

from typing import Any, Optional

from ..periodes import periode_datetime


def _une_valeur(montant: Optional[float], valeur_chaine: Optional[str]) -> dict:
    if (montant is None) == (valeur_chaine is None):
        raise ValueError("give exactly one of montant (number) or valeur_chaine (text)")
    return {"montant": montant} if montant is not None else {"chaine": valeur_chaine}


class _SaisiesMixin:

    def ajouter_element_variable(
        self, numero_dossier: str, matricule_salarie: str, *, periode: str, code: str,
        montant: Optional[float] = None, valeur_chaine: Optional[str] = None,
        controle_existence: Optional[bool] = None,
    ) -> Any:
        """Enter a variable element (its code must exist in a bonus profile used by
        the payslip computation, otherwise the next computation drops it)."""
        v = _une_valeur(montant, valeur_chaine)
        element: dict = {"periodeElementVariable": periode_datetime(periode),
                         "codeElementVariable": code}
        if "montant" in v:
            element["montantElementVariable"] = v["montant"]
        else:
            element["valeurChaineElementVariable"] = v["chaine"]
        if controle_existence is not None:
            element["controleExistenceElementVariable"] = bool(controle_existence)
        return self.call(
            "v1/ElementsVariables/SalarieAjouterElementVariable",
            {"elementVariable": element, "matriculeSalarie": matricule_salarie,
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def ajouter_prime(
        self, numero_dossier: str, matricule_salarie: str, *, periode: str, code: str,
        montant: Optional[float] = None, valeur_chaine: Optional[str] = None,
        prime_nette: Optional[bool] = None, intitule_bulletin: Optional[str] = None,
        intitule_court: Optional[str] = None,
    ) -> Any:
        """Enter a bonus (its code must exist in Silae). `intitule_bulletin` ≤ 45
        characters, `intitule_court` ≤ 10."""
        v = _une_valeur(montant, valeur_chaine)
        prime: dict = {"periodePrime": periode_datetime(periode), "codePrime": code}
        if "montant" in v:
            prime["montantPrime"] = v["montant"]
        else:
            prime["valeurChainePrime"] = v["chaine"]
        for cle, valeur in (("primeNette", prime_nette), ("intituleBulletin", intitule_bulletin),
                            ("intituleCourt", intitule_court)):
            if valeur is not None:
                prime[cle] = valeur
        return self.call(
            "v1/ElementsVariables/SalarieAjouterPrime",
            {"prime": prime, "matriculeSalarie": matricule_salarie,
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def ajouter_heures(
        self, numero_dossier: str, matricule_salarie: str, *, periode: str, code: str,
        nombre: float, ajouter: bool,
    ) -> Any:
        """Enter hours of type `code` for the month: `ajouter=True` adds to the
        existing value, `False` replaces it."""
        return self.call(
            "v1/ActivitesEtHeures/SalarieAjouterHeures",
            {"heures": {"periodeHeures": periode_datetime(periode), "codeHeures": code,
                        "nombreHeures": nombre, "ajouter": bool(ajouter)},
             "matriculeSalarie": matricule_salarie, "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )

    def confirmer_saisies(
        self, numero_dossier: str, *, periode: str, confirmer_heures: bool,
        confirmer_primes: bool,
    ) -> Any:
        """Confirm the hours and/or bonuses entered for the month."""
        return self.call(
            "v1/ElementsVariables/SalariesConfirmerSaisies",
            {"confirmationSaisies": {"periodeConfirmation": periode_datetime(periode),
                                     "confirmerHeures": bool(confirmer_heures),
                                     "confirmerPrimes": bool(confirmer_primes)},
             "numeroDossier": numero_dossier},
            numero_dossier=numero_dossier,
        )
