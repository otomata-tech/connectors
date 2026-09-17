"""Families of Nextmotion calls, composed into `NextmotionClient`.

One module per API family. Contract and scope: `../client.py`.
"""

from .calendar import _CalendarMixin
from .catalog import _CatalogMixin
from .clinics import _ClinicsMixin
from .crm import _CrmMixin
from .sales import _SalesMixin

__all__ = [
    "_CalendarMixin",
    "_CatalogMixin",
    "_ClinicsMixin",
    "_CrmMixin",
    "_SalesMixin",
]
