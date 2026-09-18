"""Familles d'appels PayFit, composées dans `PayfitClient`.

Un module par domaine d'API. Construction, transport et résolution de l'id
d'entreprise : `../client.py`. Gardes de paramètres : `../params.py`.
"""

from .absences import _AbsencesMixin
from .contracts import _ContractsMixin
from .payroll import _PayrollMixin
from .people import _PeopleMixin

__all__ = [
    "_AbsencesMixin",
    "_ContractsMixin",
    "_PayrollMixin",
    "_PeopleMixin",
]
