"""Microsoft 365 via Microsoft Graph — SharePoint sites, document libraries and
OneDrive files, on behalf of a signed-in person (OAuth 2.0 delegated access)."""

from .auth import FILES_SCOPES, Grant, MicrosoftAuthError, MicrosoftGrantExpired
from .client import GraphClient

__all__ = ["FILES_SCOPES", "GraphClient", "Grant", "MicrosoftAuthError",
           "MicrosoftGrantExpired"]
