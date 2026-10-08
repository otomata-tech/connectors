"""Typeform — workspaces, forms (read, create, replace, patch, delete), responses
(read, delete, summary) and webhooks."""

from .client import REGIONS, TypeformClient

__all__ = ["REGIONS", "TypeformClient"]
