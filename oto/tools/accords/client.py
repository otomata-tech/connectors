"""HTTP client for the ACCO index (company agreements), exposed by oto-mcp."""

import os
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require

_DEFAULT_BASE_URL = "https://mcp.oto.cx"


class AccordsError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"accords API {status}: {detail}")


class AccordsClient:
    """Search the ACCO index via `/api/fr/accords/*`.

    Example:
        acc = AccordsClient()
        res = acc.search(idcc="1486", themes=["111", "112"], limit=20)
        one = acc.get("ACCOTEXT000054284583")
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (
            base_url or os.environ.get("OTO_API_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.token = require(token, "OTO_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    def _raise(self, r: requests.Response) -> Any:
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise AccordsError(r.status_code, detail)
        return r.json()

    def search(self, query: Optional[str] = None, themes: Optional[List[str]] = None,
               nature: Optional[str] = None, siren: Optional[str] = None,
               siret: Optional[str] = None, idcc: Optional[str] = None,
               departement: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None, latest_per_siret: bool = False,
               sort_by: str = "date", sort_dir: str = "desc",
               limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """A page of results + `total_count` (returned even with limit=1: enough to
        size a campaign without pulling back the rows).

        `idcc`: a convention code. The index stores it without a leading zero, the
        server accepts both forms — "0573" as well as "573".
        """
        payload = {
            "query": query, "themes": themes, "nature": nature, "siren": siren,
            "siret": siret, "idcc": idcc, "departement": departement,
            "date_from": date_from, "date_to": date_to,
            "latest_per_siret": latest_per_siret,
            "sort_by": sort_by, "sort_dir": sort_dir, "limit": limit, "offset": offset,
        }
        return self._raise(self.session.post(
            f"{self.base_url}/api/fr/accords/search", json=payload, timeout=60))

    def get(self, id_or_numero: str) -> Dict[str, Any]:
        """One agreement by its DILA id (`ACCOTEXT000…`) or its filing number (`T…`)."""
        return self._raise(self.session.get(
            f"{self.base_url}/api/fr/accords/{id_or_numero}", timeout=30))

    def themes(self) -> List[Dict[str, Any]]:
        """Theme nomenclature (code + label) for building a filter."""
        res = self._raise(self.session.get(
            f"{self.base_url}/api/fr/accords/themes", timeout=30))
        return res.get("themes", res) if isinstance(res, dict) else res

    def sirens_by_idcc(self, idccs: List[str], limit_per_idcc: int = 1000,
                       **filters: Any) -> List[str]:
        """Distinct SIRENs covered by SEVERAL collective agreements.

        A single company often carries 3-4 IDCCs (construction, typically) and the
        upstream API only accepts one code per request: without this helper, every
        caller rewrites the loop and the deduplication — 1,094 rows for 386 distinct
        companies on a real case. Order of first appearance is kept (deterministic).
        """
        seen: Dict[str, None] = {}
        for code in idccs:
            res = self.search(idcc=str(code).strip(), limit=limit_per_idcc, **filters)
            for row in res.get("results", []):
                siret = row.get("siret") or ""
                siren = row.get("siren") or (siret[:9] if len(siret) >= 9 else "")
                if siren:
                    seen.setdefault(siren, None)
        return list(seen)
