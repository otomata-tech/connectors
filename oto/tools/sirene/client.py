"""SireneClient — logique dans la lib partagée france-opendata (source unique).

Variante oto : la clé est toujours fournie par le consommateur. Le client amont
se replie sur l'environnement du process quand une clé manque ; ce repli est
neutralisé ici, la lib ne lit aucun secret.
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
