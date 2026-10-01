"""Microsoft 365 via Microsoft Graph — SharePoint sites, document libraries and
OneDrive files, with the app-only credential of an Entra app (client credentials)."""

from .auth import MicrosoftAuthError
from .client import GraphClient

__all__ = ["GraphClient", "MicrosoftAuthError"]
