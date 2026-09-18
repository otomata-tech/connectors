"""Les personnes : l'annuaire des collaborateurs, leurs bulletins, leurs
titres-restaurant.

Ce mixin n'est jamais instancié seul : il est composé dans `PayfitClient`, qui
fournit le transport (`_get`, `_get_file`, `_post`, `_company_path`).

⚠️ **Un bulletin, c'est deux appels et deux natures.** `list_payslips` rend les
MÉTADONNÉES (`year`, `month`, `contractId`, `payslipId`, `payslipUrl`) ;
`get_payslip` rend le PDF. **L'API ne sert jamais les LIGNES d'un bulletin** :
brut, net, cotisation par cotisation, il n'existe aucun endpoint qui les rende
en données. Les seuls montants structurés de cette API sont les écritures
comptables (`list_accounting_entries`).

⚠️ `list_payslips` ne pagine pas et ne se filtre pas : elle rend TOUS les
bulletins du collaborateur, tous contrats confondus.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..params import clean
from ..params import ident as _id
from ..params import month as _month
from ..params import page as _page


class _PeopleMixin:
    """Collaborateurs, bulletins, titres-restaurant."""

    # --- annuaire -----------------------------------------------------------

    def list_collaborators(self, *, limit: int = 50, cursor: Optional[str] = None,
                           email: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/collaborators.

        Scope `collaborators:read` ; les champs sensibles (NIR, IBAN/BIC,
        naissance, coordonnées personnelles) n'arrivent que si la clé porte AUSSI
        `collaborators:social-security:read`, `collaborators:bank-info:read`,
        `collaborators:personal:read` ou `collaborators:legal-identity:read`.

        Args:
            email: only collaborators with this email in one of their contracts
                (the login email is not searched).
        """
        return self._get(self._company_path("/collaborators"),
                         **_page(limit, cursor), email=email)

    def get_collaborator(self, collaborator_id: str) -> Any:
        """GET /companies/{companyId}/collaborators/{collaboratorId}."""
        return self._get(self._company_path(
            f"/collaborators/{_id(collaborator_id, 'collaborator_id')}"))

    def create_collaborator(self, *, first_name: str, last_name: str,
                            personal_email: str, other_name: Optional[str] = None,
                            social_security_number: Optional[str] = None,
                            personal_address: Optional[dict] = None,
                            birth_information: Optional[dict] = None,
                            personal_phone_number: Optional[str] = None,
                            number_of_children: Optional[int] = None,
                            gender: Optional[str] = None,
                            invite_collaborator: Optional[bool] = None) -> Any:
        """POST /companies/{companyId}/collaborators — crée un collaborateur.

        Scope `collaborators:write`. Rend `{collaboratorId}`. Un collaborateur
        créé ici n'a **pas encore de contrat** : `create_contract` est l'étape
        suivante, et c'est elle qui le fait entrer dans la paie.

        ⚠️ `invite_collaborator=True` **envoie un e-mail** à la personne pour
        qu'elle crée son accès PayFit : un effet visible hors du système.

        Args:
            first_name / last_name / personal_email: les trois champs exigés.
            other_name: nom d'usage (FR), segundo apellido (ES), middle name (UK).
            social_security_number: NIR — longueur imposée par pays (FR 15,
                ES 14, GB 12).
            personal_address: `{streetNumber, addressFirstLine, addressSecondLine,
                city, state, postCode, country}` — `country` en code ISO 2
                lettres ; `streetNumber`, `addressFirstLine`, `city`, `postCode`
                et `country` sont exigés dès que l'objet est fourni.
            birth_information: `{birthDate, birthPlace, birthCountry}`,
                `birthDate` en AAAA-MM-JJ dans le passé.
            number_of_children: entier de 0 à 20.
            gender: `MALE` ou `FEMALE` — le jeu FERMÉ de l'API, pas le nôtre.
            invite_collaborator: envoie l'e-mail d'invitation.
        """
        return self._post(self._company_path("/collaborators"), clean({
            "firstName": first_name, "lastName": last_name,
            "personalEmail": personal_email, "otherName": other_name,
            "socialSecurityNumber": social_security_number,
            "personalAddress": personal_address,
            "birthInformation": birth_information,
            "personalPhoneNumber": personal_phone_number,
            "numberOfChildren": number_of_children, "gender": gender,
            "inviteCollaborator": invite_collaborator,
        }))

    # --- bulletins ----------------------------------------------------------

    def list_payslips(self, collaborator_id: str) -> Any:
        """GET /companies/{companyId}/collaborators/{collaboratorId}/payslips.

        Scope `contracts:payslips:read`. Rend `{payslips: [{year, month,
        contractId, payslipId, payslipUrl}]}` — des métadonnées, jamais de
        montants.
        """
        return self._get(self._company_path(
            f"/collaborators/{_id(collaborator_id, 'collaborator_id')}/payslips"))

    def get_payslip(self, collaborator_id: str, contract_id: str,
                    payslip_id: str) -> Dict[str, Any]:
        """GET …/collaborators/{id}/contracts/{id}/payslips/{id} — le PDF.

        Scope `contracts:payslips:read`. Rend `{data: bytes, filename, mimetype}`.
        Les trois identifiants viennent d'une même entrée de `list_payslips`
        (plus l'id du collaborateur) : le `contractId` d'une ligne n'est pas
        interchangeable avec un autre contrat de la personne.
        """
        col = _id(collaborator_id, "collaborator_id")
        con = _id(contract_id, "contract_id")
        pay = _id(payslip_id, "payslip_id")
        return self._get_file(
            self._company_path(f"/collaborators/{col}/contracts/{con}/payslips/{pay}"),
            filename=f"payfit-bulletin-{pay}.pdf", mimetype="application/pdf")

    # --- titres-restaurant --------------------------------------------------

    def list_meal_vouchers(self, date: str, *, limit: int = 50,
                           cursor: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/collaborators/meal-vouchers — 🇫🇷.

        Scope `collaborators:meal-vouchers:read`. Par collaborateur et pour le
        mois : nombre de titres, valeur faciale, part patronale, part salariale,
        éligibilité des jours non travaillés.

        Args:
            date: le mois, AAAAMM.
        """
        return self._get(self._company_path("/collaborators/meal-vouchers"),
                         date=_month(date), **_page(limit, cursor))
