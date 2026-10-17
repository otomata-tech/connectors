"""Amplemarket call families, composed into `AmplemarketClient`.

One module per API domain. Contract details: `../client.py`.
"""

from .engagement import EngagementMixin
from .prospecting import ProspectingMixin
from .workspace import WorkspaceMixin

__all__ = ["EngagementMixin", "ProspectingMixin", "WorkspaceMixin"]
