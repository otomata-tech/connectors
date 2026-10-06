"""SIRENE stock — client HTTP vers `mcp.oto.cx/api/sirene/*`.

The full INSEE parquet lives server-side, queried via DuckDB. This class no longer
downloads anything locally — it makes authenticated REST calls.

Auth: long-lived token stored in the `OTO_API_KEY` secret (issued from
`app.oto.ninja/account` → "tokens cli"). URL override: `OTO_API_URL`.

Use cases covered:
- `get_headquarters_addresses(sirens)` — batch enrichment (1 HTTP call → 1 server
  scan for the whole list, via POST /api/sirene/headquarters).
- `get_all_establishments(siren)` — all the establishments of a company.
- `lookup_siret(siret)` — precise fetch by SIRET.
- `search(...)` — multi-criteria search (NAF, municipality, postal code, brand name, denomination).

No local cache — every call is HTTP. If you do > 1000 lookups, consider
batching server-side (to be developed if needed).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require


_DEFAULT_BASE_URL = "https://mcp.oto.cx"


class SireneStockError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"sirene_stock {status}: {detail}")


class SireneStock:
    """HTTP client over `/api/sirene/*` exposed by oto-mcp.

    Example:
        stock = SireneStock()
        siege = stock.lookup_siege("443061841")
        ets = stock.get_all_establishments("443061841")
        addresses = stock.get_headquarters_addresses(["443061841", "552032534"])
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (
            base_url
            or os.environ.get("OTO_API_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.token = require(token, "OTO_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    # --- low-level ------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        r = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=30)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise SireneStockError(r.status_code, detail)
        return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        # wide timeout: a server-side batch scan (remote parquet) can take
        # a few dozen seconds for a big list.
        r = self.session.post(f"{self.base_url}{path}", json=payload, timeout=180)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise SireneStockError(r.status_code, detail)
        return r.json()

    # --- normalisation --------------------------------------------------------

    @staticmethod
    def _coord(value: Any) -> Optional[float]:
        """Lambert coordinate, or None when INSEE does not publish it.

        The stock carries textual SENTINELS, not just numbers:
        a non-diffusible establishment comes out as `[ND]` in the
        geolocation columns. A bare `float()` then crashed the ENTIRE scan — an
        `--all` on a NAF (~10,000 establishments) died on the first
        non-diffusible row (signal #358). A missing coordinate is an ordinary
        missing datum: the rest of the record (address, NAF,
        headcount) is valid and must come out."""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None      # `[ND]` and any other future sentinel

    @staticmethod
    def _normalize(etab: dict) -> dict:
        """Normalize an establishment dict (INSEE snake_case) → stable shape
        for the historical consumers (street, postal_code, city, status…).
        """
        if not etab:
            return etab
        num = (etab.get("numero_voie") or "").strip()
        type_voie = (etab.get("type_voie") or "").strip()
        voie = (etab.get("libelle_voie") or "").strip()
        street = " ".join(p for p in (num, type_voie, voie) if p) or None
        out = {
            "siren": etab.get("siren"),
            "siret": etab.get("siret"),
            "is_headquarters": bool(etab.get("is_siege")),
            "street": street,
            "postal_code": etab.get("code_postal"),
            "city": etab.get("libelle_commune"),
            "code_commune": etab.get("code_commune"),
            "status": "active" if etab.get("etat") == "A" else "closed",
            "naf": etab.get("naf"),
            "denomination": etab.get("denomination"),
            "enseigne": etab.get("enseigne_1") or etab.get("enseigne_2") or etab.get("enseigne_3"),
            "tranche_effectifs": etab.get("tranche_effectifs"),
            "date_creation": etab.get("date_creation"),
        }
        x = SireneStock._coord(etab.get("lambert_x"))
        if x is not None:
            out["lambert_x"] = x
            out["lambert_y"] = SireneStock._coord(etab.get("lambert_y"))
        return out

    # --- high-level (legacy API preserved) -----------------------------------

    def get_headquarters_addresses(self, sirens: List[str]) -> Dict[str, Dict[str, Any]]:
        """Headquarters address for each SIREN. Returns {siren: {street, postal_code, city, status, ...}}.

        True batch: ONE HTTP call → ONE parquet scan server-side for the whole
        list (vs one call per SIREN). Essential on remote parquet. The
        addresses returned by /headquarters are already normalized server-side.

        For SIRENs not found: absent from the dict (no head office on the server side).
        """
        clean = [str(s) for s in sirens]
        if not clean:
            return {}
        data = self._post("/api/sirene/headquarters", {"sirens": clean})
        return data.get("headquarters", {})

    def get_all_establishments(self, siren: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """All the establishments of a SIREN (head office + secondary)."""
        params = {"siren": str(siren), "active_only": "true" if active_only else "false"}
        data = self._get("/api/sirene/etablissements", params=params)
        return [self._normalize(e) for e in data.get("items", [])]

    # --- new methods ---------------------------------------------------

    def lookup_siege(self, siren: str) -> Optional[Dict[str, Any]]:
        """Head office (headquarters) of a SIREN, or None."""
        data = self._get("/api/sirene/siege", params={"siren": str(siren)})
        siege = data.get("siege")
        return self._normalize(siege) if siege else None

    def lookup_siret(self, siret: str) -> Optional[Dict[str, Any]]:
        """A precise establishment by SIRET."""
        data = self._get("/api/sirene/siret", params={"siret": str(siret)})
        etab = data.get("etablissement")
        return self._normalize(etab) if etab else None

    def search(
        self,
        naf: Optional[str] = None,
        code_commune: Optional[str] = None,
        code_postal: Optional[str] = None,
        departement: Optional[str] = None,
        denomination: Optional[str] = None,
        enseigne: Optional[str] = None,
        active_only: bool = True,
        sieges_only: bool = False,
        tranche_effectifs: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Multi-criteria search server-side (DuckDB). All filters AND.

        `tranche_effectifs`: INSEE TEFEN codes separated by commas
        (e.g. "22,31,32" = 50 employees and more). This is the criterion that makes you pick
        this route rather than the MCP tool — enumerating hundreds of head offices PER
        SIZE without passing the JSON through the agent's context (signal #331).

        Returns: {items: [...], count: N, limit: N, offset: N}
        """
        params: dict[str, Any] = {
            "active_only": "true" if active_only else "false",
            "sieges_only": "true" if sieges_only else "false",
            "limit": int(limit),
            "offset": int(offset),
        }
        if naf:
            params["naf"] = naf
        if code_commune:
            params["code_commune"] = code_commune
        if code_postal:
            params["code_postal"] = code_postal
        if departement:
            params["departement"] = departement
        if denomination:
            params["denomination"] = denomination
        if enseigne:
            params["enseigne"] = enseigne
        if tranche_effectifs:
            params["tranche_effectifs"] = tranche_effectifs
        data = self._get("/api/sirene/search", params=params)
        data["items"] = [self._normalize(e) for e in data.get("items", [])]
        return data

    def info(self) -> Dict[str, Any]:
        """Metadata of the parquet file server-side (size, mtime, total_rows)."""
        return self._get("/api/sirene/info")
