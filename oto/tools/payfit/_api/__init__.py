"""PayFit call families, composed into `PayfitClient`.

One module per API domain. Construction, transport and company id resolution:
`../client.py`. Parameter guards: `../params.py`.
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
