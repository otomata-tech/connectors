"""L'entreprise et la PAIE d'un mois : état du cycle, écritures comptables,
export comptable, fichier de virement, documents, mutuelle et prévoyance.

Ce mixin n'est jamais instancié seul : il est composé dans `PayfitClient`, qui
fournit le transport (`_get`, `_get_file`, `_company_path`).

⚠️ **Deux racines de collection, pas une racine et un suffixe** : l'entreprise
« monde » vit sous `/companies/{id}`, la variante française sous
`/companies-fr/{id}`. La seconde ajoute SIREN, SIRET, l'adresse légale et la
méthode de proratisation de la mutuelle ; elle ne remplace pas la première.

⚠️ **`accounting` et `accounting-v2` ne sont pas deux versions du même format** :
`accounting` rend un FICHIER (CSV à points-virgules, tel que le cabinet
l'importe), `accounting-v2` rend les MÊMES écritures en JSON, ligne à ligne
(`operationDate`, `accountId`, `accountName`, `debit`, `credit`, `contractId`,
`employeeFullName`, codes analytiques). Ce qu'un agent peut analyser est la v2 ;
ce qu'un comptable importe est la v1. Les deux sont servies.

Le coût employeur et les charges n'ont **aucun endpoint propre** : ils se lisent
dans les écritures (comptes 641x, 645x, 6417x pour les avantages en nature) —
c'est la seule voie que l'API documente.
"""
from __future__ import annotations

from typing import Any, Dict

from ..params import month as _month
from ..params import ident as _id


class _PayrollMixin:
    """Entreprise, cycle de paie, comptabilité, documents, mutuelle/prévoyance."""

    # --- entreprise ---------------------------------------------------------

    def get_company(self, *, fr: bool = False) -> Any:
        """GET /companies/{companyId} — ou /companies-fr/{companyId} si `fr`.

        Aucun scope requis. La variante FR ajoute `siren`, `siret`, l'adresse
        légale et `healthInsuranceProrationMethod`.
        """
        return self._get(self._company_path(fr=fr))

    def get_payroll_status(self, date: str) -> Any:
        """GET /companies/{companyId}/payroll-status — `{status, executionEndDate}`.

        `status` vaut `completed` ou `not_completed` : c'est ce qui dit si les
        chiffres du mois sont définitifs. Aucun scope requis.

        Args:
            date: le mois, AAAAMM.
        """
        return self._get(self._company_path("/payroll-status"), date=_month(date))

    # --- comptabilité et virements ------------------------------------------

    def list_accounting_entries(self, date: str) -> Any:
        """GET /companies/{companyId}/accounting-v2 — les écritures du mois, en JSON.

        Scope `accounting:read`. Rend un TABLEAU nu (pas d'enveloppe `meta`) et
        ne pagine pas : le mois entier arrive d'un coup.

        Args:
            date: le mois, AAAAMM.
        """
        return self._get(self._company_path("/accounting-v2"), date=_month(date))

    def get_accounting_export(self, date: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/accounting — le journal comptable en FICHIER.

        Scope `accounting:read`. Rend `{data: bytes, filename, mimetype}`.

        Args:
            date: le mois, AAAAMM.
        """
        m = _month(date)
        return self._get_file(self._company_path("/accounting"),
                              filename=f"payfit-comptabilite-{m}.csv",
                              mimetype="text/csv", date=m)

    def get_payment_file(self, date: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/payment-files — le fichier de virement du mois.

        Scope `payment-files:read`. Rend `{data: bytes, filename, mimetype}`.

        ⚠️ Ce fichier porte les **coordonnées bancaires des salariés** : c'est un
        ordre de virement destiné à une banque, pas un état de gestion.

        Args:
            date: le mois, AAAAMM.
        """
        m = _month(date)
        return self._get_file(self._company_path("/payment-files"),
                              filename=f"payfit-virements-{m}.txt",
                              mimetype="application/octet-stream", date=m)

    # --- documents ----------------------------------------------------------

    def get_document(self, document_id: str) -> Dict[str, Any]:
        """GET /companies/{companyId}/documents/{documentId} — un PDF de l'entreprise.

        Scope `contracts:payslips:read` ou `pension:read` selon le document. Rend
        `{data: bytes, filename, mimetype}`. C'est le chemin de récupération des
        `documentId` rendus par `list_income_tax_documents` et
        `list_auto_enrolment_documents`.
        """
        doc = _id(document_id, "document_id")
        return self._get_file(self._company_path(f"/documents/{doc}"),
                              filename=f"payfit-{doc}.pdf", mimetype="application/pdf")

    def list_income_tax_documents(self) -> Any:
        """GET /companies/{companyId}/income-taxes-documents — 🇬🇧 uniquement.

        Scope `contracts:payslips:read`. Documents fiscaux britanniques (P45,
        P60…) : `{documents: [{documentId, type, year, month, contractId,
        documentUrl, createdAt}]}`. **Sans équivalent français** : l'API ne sert
        ni DSN ni attestation fiscale FR.
        """
        return self._get(self._company_path("/income-taxes-documents"))

    def list_auto_enrolment_documents(self) -> Any:
        """GET /companies/{companyId}/auto-enrolment-documents — 🇬🇧 uniquement.

        Scope `pension:read`. Documents d'affiliation automatique à la retraite
        britannique.
        """
        return self._get(self._company_path("/auto-enrolment-documents"))

    # --- mutuelle et prévoyance (catalogue d'entreprise) --------------------

    def list_health_insurance_contracts(self) -> Any:
        """GET /companies/{companyId}/health-insurance-contracts — 🇫🇷.

        Scope `health-insurance:read`. Les contrats de MUTUELLE souscrits par
        l'entreprise : référence, population, option, base et taux de cotisation
        patronale et salariale, et les `affiliatedContractIds` (les contrats de
        travail affiliés).
        """
        return self._get(self._company_path("/health-insurance-contracts"))

    def list_provident_fund_contracts(self) -> Any:
        """GET /companies/{companyId}/provident-fund-contracts — 🇫🇷.

        Scope `health-insurance:read` (le même que la mutuelle, ce n'est pas une
        faute de frappe). Les contrats de PRÉVOYANCE de l'entreprise.
        """
        return self._get(self._company_path("/provident-fund-contracts"))
