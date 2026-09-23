"""Yousign — signature électronique : demandes de signature, documents,
signataires, activation, récupération du document signé."""

from .client import YousignClient

__all__ = ["YousignClient"]
