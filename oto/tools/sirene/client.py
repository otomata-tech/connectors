"""SireneClient — logic in the shared france-opendata lib (single source).

oto variant: the key is always supplied by the consumer. The upstream client
falls back to the process environment when a key is missing; that fallback is
neutralized here, the lib reads no secret.
"""
from france_opendata.sirene import SireneClient as _BaseSireneClient, EMPLOYEE_RANGES

from ..common.credentials import MissingCredential

__all__ = ["SireneClient", "EMPLOYEE_RANGES"]


class SireneClient(_BaseSireneClient):
    def __init__(self, api_key: str = None, secret: str = None):
        if not api_key and not secret:
            raise MissingCredential("SIRENE_API_KEY or SIRENE_SECRET")
        super().__init__(api_key=api_key, secret=secret)
        self.api_key, self.secret = api_key, secret
