"""Silae call families, composed into `SilaeClient`.

One module per API domain. Transport and contract: `../client.py`.
"""

from .bulletins import _BulletinsMixin
from .declarations import _DeclarationsMixin
from .dossiers import _DossiersMixin
from .editions import _EditionsMixin
from .saisies import _SaisiesMixin
from .salaries import _SalariesMixin

__all__ = [
    "_BulletinsMixin",
    "_DeclarationsMixin",
    "_DossiersMixin",
    "_EditionsMixin",
    "_SaisiesMixin",
    "_SalariesMixin",
]
