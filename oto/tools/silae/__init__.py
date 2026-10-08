"""Silae Paie REST API client (French payroll)."""

from .auth import SilaeAuthError
from .client import SilaeClient

__all__ = ["SilaeAuthError", "SilaeClient"]
