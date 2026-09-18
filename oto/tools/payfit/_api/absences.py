"""Les absences : lecture, création d'une absence DÉJÀ validée, annulation.

Ce mixin n'est jamais instancié seul : il est composé dans `PayfitClient`, qui
fournit le transport (`_get`, `_post`, `_delete`, `_company_path`).

⚠️ **`POST /absences` crée une absence APPROUVÉE**, pas une demande. L'API n'a
aucun endpoint d'approbation, de refus ou de solde : ce qui est écrit ici entre
directement en paie. Symétriquement, `DELETE` **annule** l'absence et porte son
commentaire dans un CORPS JSON — un DELETE à corps, inhabituel, mais c'est ce
que la spec documente.

⚠️ **Les deux jeux de types ne coïncident pas.** Ce qu'on peut LIRE
(`AbsenceType`, ~50 valeurs, dont `other` quand PayFit n'a pas encore tranché)
et ce qu'on peut CRÉER (`CreateAbsenceType`, ~75 valeurs) se recoupent sans
s'inclure : la création détaille les événements familiaux (`fr_mariage_salarie`,
`fr_deces_conjoint`…) que la lecture regroupe, et elle n'a ni `fr_maternite` ni
`fr_accident_travail`. Aucune des deux listes n'est recopiée ici : une valeur inconnue
repart en 400 nommé par PayFit, là où une liste figée côté client se périmerait
en silence au prochain type ajouté.

**Ce que l'API ne sait pas faire** : il n'y a ni solde de congés, ni compteur
(CP acquis/pris, RTT restants), ni lecture unitaire d'une absence.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence, Union

from ..params import clean
from ..params import ident as _id
from ..params import page as _page

# Les moments de journée que l'API accepte aux deux bornes d'une absence. Jeu
# FERMÉ côté PayFit et stable (une demi-journée n'a pas de quatrième forme) :
# le refuser ici évite un 400 qui ne nomme pas le champ.
MOMENTS = ("beginning-of-day", "middle-of-day", "end-of-day")


def _moment(value: Any, name: str) -> str:
    if value not in MOMENTS:
        raise ValueError(f"{name} doit valoir l'un de {', '.join(MOMENTS)} — "
                         f"reçu {value!r}.")
    return value


class _AbsencesMixin:
    """Absences : lecture, création, annulation."""

    def list_absences(self, *, limit: int = 50, cursor: Optional[str] = None,
                      contract_id: Optional[str] = None,
                      status: Optional[Union[str, Sequence[str]]] = None,
                      begin_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> Any:
        """GET /companies/{companyId}/absences.

        Scope `time:read`.

        Args:
            contract_id: only this contract's absences.
            status: approved (upstream default) | pending_approval | declined |
                cancelled | pending_cancellation | all — one or several.
            begin_date / end_date: YYYY-MM-DD; absences overlapping the window.
        """
        if contract_id is not None:
            contract_id = _id(contract_id, "contract_id")
        if status is not None and not isinstance(status, str):
            status = ",".join(status)
        return self._get(self._company_path("/absences"), **_page(limit, cursor),
                         contractId=contract_id, status=status or None,
                         beginDate=begin_date, endDate=end_date)

    def create_absence(self, *, contract_id: str, absence_type: str,
                       start_date: str, end_date: str,
                       start_moment: str = "beginning-of-day",
                       end_moment: str = "end-of-day") -> Any:
        """POST /companies/{companyId}/absences — une absence déjà VALIDÉE.

        Scope `time:write`. Rend `{id}`.

        Args:
            contract_id: le contrat concerné.
            absence_type: une valeur de `CreateAbsenceType` (`fr_conges_payes`,
                `fr_rtt`, `fr_sans_solde`, `fr_maladie_ordinaire`…) — le jeu
                exact est celui de la spec PayFit, pas une liste tenue ici.
            start_date / end_date: AAAA-MM-JJ.
            start_moment / end_moment: `beginning-of-day`, `middle-of-day` ou
                `end-of-day` — les défauts couvrent une absence en jours pleins.
        """
        return self._post(self._company_path("/absences"), {
            "contractId": _id(contract_id, "contract_id"),
            "type": absence_type,
            "startDate": {"date": start_date,
                          "moment": _moment(start_moment, "start_moment")},
            "endDate": {"date": end_date,
                        "moment": _moment(end_moment, "end_moment")},
        })

    def cancel_absence(self, absence_id: str, *,
                       comment: Optional[str] = None) -> Any:
        """DELETE /companies/{companyId}/absences/{absenceId} — annule l'absence.

        Scope `time:write`. Répond 204 sans corps.

        Args:
            comment: commentaire consigné sur l'annulation (facultatif).
        """
        return self._delete(
            self._company_path(f"/absences/{_id(absence_id, 'absence_id')}"),
            clean({"comment": comment}) or None)
