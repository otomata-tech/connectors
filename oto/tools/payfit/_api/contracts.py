"""Les contrats de travail : lecture (deux variantes), création, temps de travail
réalisé, affiliation à la mutuelle et à la prévoyance.

Ce mixin n'est jamais instancié seul : il est composé dans `PayfitClient`, qui
fournit le transport (`_get`, `_post`, `_put`, `_company_path`).

⚠️ **`/contracts-fr` n'est pas `/contracts` + des champs optionnels** : c'est une
autre collection, et c'est la SEULE qui rende la nature du contrat
(`natureContratDsn` — 01 CDI, 02 CDD…), le statut conventionnel, l'IDCC de la
convention collective, le motif de rupture, la modalité de temps de travail
(`standard`, `forfait_heures`, `forfait_jours`…), le statut de cadre dirigeant,
les contrats de mutuelle et de prévoyance affiliés, et le NIR. Pour une
entreprise française, c'est elle qu'il faut lire ; `country` de l'entreprise le
dit.

⚠️ Le paramètre `fields=securite-sociale` de `/contracts-fr` est **déprécié** et
n'est jamais envoyé : le NIR arrive aujourd'hui avec le scope de la clé.

**Ce que l'API ne sait pas faire sur un contrat** : il n'y a ni historique des
avenants, ni modification d'un contrat existant, ni coefficient hiérarchique
séparé, ni endpoint de rupture. La seule écriture est la CRÉATION.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ..params import clean
from ..params import ident as _id
from ..params import month as _month
from ..params import page as _page


def _ids(values: Any, name: str) -> list:
    """Une liste d'identifiants, chacun passé à la même garde qu'un segment
    d'URL. Ils ne voyagent pas dans le chemin ici, mais un id malformé envoyé en
    corps produit un 400 opaque — autant le nommer."""
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ValueError(f"{name} doit être une LISTE d'identifiants — reçu {values!r}.")
    if not values:
        raise ValueError(f"{name} ne peut pas être vide.")
    return [_id(v, name) for v in values]


class _ContractsMixin:
    """Contrats, temps de travail, affiliations mutuelle/prévoyance."""

    # --- lecture ------------------------------------------------------------

    def list_contracts(self, *, limit: int = 50, cursor: Optional[str] = None,
                       include_in_progress: Optional[bool] = None,
                       fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts (or /contracts-fr when `fr`).

        Scope `contracts:read`. Active, pending and last year's archived
        contracts — **pas l'historique complet** de l'entreprise.

        Args:
            include_in_progress: also contracts still being created.
            fr: the French variant, with DSN fields.
        """
        flag = None if include_in_progress is None else (
            "true" if include_in_progress else "false")
        return self._get(self._company_path("/contracts-fr" if fr else "/contracts"),
                         **_page(limit, cursor), includeInProgressContracts=flag)

    def get_contract(self, contract_id: str, *, fr: bool = False) -> Any:
        """GET /companies/{companyId}/contracts/{contractId} (or /contracts-fr/…)."""
        base = "/contracts-fr" if fr else "/contracts"
        return self._get(self._company_path(
            f"{base}/{_id(contract_id, 'contract_id')}"))

    def list_worked_time(self, date: str, *, limit: int = 50,
                         cursor: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/contracts/time — 🇫🇷, le temps du mois.

        Scope `time:read`. Par contrat : `effectiveWorkedTime` (réalisé),
        `payedWorkedTime` (payé) et `workTimeUnit`.

        ⚠️ C'est un **agrégat mensuel**, pas un planning : l'API ne sert ni
        horaires, ni pointages, ni jours travaillés un par un.

        Args:
            date: le mois, AAAAMM.
        """
        return self._get(self._company_path("/contracts/time"),
                         date=_month(date), **_page(limit, cursor))

    # --- création -----------------------------------------------------------

    def create_contract(self, collaborator_id: str, *, job_title: str,
                        start_date: str) -> Any:
        """POST /companies/{companyId}/collaborators/{collaboratorId}/contracts — 🇫🇷.

        Scope `collaborators:contracts:write`. Crée le contrat d'un collaborateur
        déjà existant ; c'est lui qui le fait entrer dans la paie.

        Args:
            job_title: l'intitulé du poste.
            start_date: AAAA-MM-JJ.
        """
        col = _id(collaborator_id, "collaborator_id")
        return self._post(self._company_path(f"/collaborators/{col}/contracts"),
                          {"jobTitle": job_title, "startDate": start_date})

    # --- mutuelle et prévoyance d'UN contrat ---------------------------------

    def set_health_insurance(self, contract_id: str, *,
                             health_insurance_contract_ids: Sequence[str],
                             employee_is_exempted: Optional[bool] = None) -> Any:
        """PUT /companies/{companyId}/contracts-fr/{contractId}/health-insurance.

        Scope `health-insurance:write`. **Remplace** l'affiliation mutuelle du
        contrat par la liste fournie : ce n'est pas un ajout, une liste qui omet
        un contrat l'en désaffilie.

        Args:
            health_insurance_contract_ids: ids de
                `list_health_insurance_contracts`.
            employee_is_exempted: le salarié est dispensé d'adhésion.
        """
        con = _id(contract_id, "contract_id")
        return self._put(
            self._company_path(f"/contracts-fr/{con}/health-insurance"),
            clean({"healthInsuranceContractIds": _ids(
                health_insurance_contract_ids, "health_insurance_contract_ids"),
                "employeeIsExempted": employee_is_exempted}))

    def set_provident_fund(self, contract_id: str, *,
                           provident_fund_contract_ids: Sequence[str]) -> Any:
        """PUT /companies/{companyId}/contracts-fr/{contractId}/provident-fund.

        Scope `health-insurance:write` (le même que la mutuelle). **Remplace**
        l'affiliation prévoyance du contrat.

        Args:
            provident_fund_contract_ids: ids de `list_provident_fund_contracts`.
        """
        con = _id(contract_id, "contract_id")
        return self._put(
            self._company_path(f"/contracts-fr/{con}/provident-fund"),
            {"providentFundContractIds": _ids(
                provident_fund_contract_ids, "provident_fund_contract_ids")})

    def request_health_insurance_regularization(
            self, contract_id: str, *,
            health_insurance_contract_ids: Sequence[str],
            effective_date: str) -> Any:
        """POST /companies/{companyId}/contracts-fr/{contractId}/regularization.

        Scope `health-insurance:write`. Demande une régularisation rétroactive
        des cotisations mutuelle : elle **recalcule des cotisations déjà passées
        en paie** et se répercute sur un bulletin.

        Args:
            effective_date: AAAA-MM-JJ, la date d'effet de la régularisation.
        """
        con = _id(contract_id, "contract_id")
        return self._post(
            self._company_path(f"/contracts-fr/{con}/regularization"),
            {"healthInsuranceContractIds": _ids(
                health_insurance_contract_ids, "health_insurance_contract_ids"),
             "effectiveDate": effective_date})
