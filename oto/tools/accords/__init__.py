"""Company agreements (ACCO index) — HTTP client for `/api/fr/accords/*`.

The index lives in the FOD service, on a private network: a workstation cannot
query it directly. We therefore go through oto-mcp, which republishes it — same
reason for existing as `SireneStock`, and same authentication contract.

Auth: long-lived `OTO_API_KEY` token (dashboard → "cli & api tokens").
URL override: `OTO_API_URL`.
"""

from .client import AccordsClient, AccordsError

__all__ = ["AccordsClient", "AccordsError"]
