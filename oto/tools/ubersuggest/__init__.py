"""Ubersuggest — keyword research, traffic, backlinks, site audits and rank tracking,
on behalf of a signed-in person (OAuth 2.1 public client, remote MCP transport)."""
from . import auth
from .auth import SCOPES, Grant, UbersuggestAuthError, UbersuggestGrantExpired
from .client import TOOLS, UbersuggestClient, UbersuggestToolError

__all__ = ["SCOPES", "TOOLS", "Grant", "UbersuggestAuthError", "UbersuggestClient",
           "UbersuggestGrantExpired", "UbersuggestToolError", "auth"]
