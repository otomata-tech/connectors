"""Planity auth chain: email/password -> Firebase basic token -> Planity custom
token -> Firebase enriched idToken (with per-business claims).

The enriched token is what Firebase RTDB rules actually check against.
It carries {businessId_1: 1, businessId_2: 1, ...} claims.

Tokens expire after 1h; we refresh proactively.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import HTTP_TIMEOUT, PlanityEndpoints


@dataclass
class PlanityTokens:
    id_token: str
    refresh_token: str
    uid: str
    business_ids: list[str]
    expires_at: float  # unix timestamp


def _decode_jwt_claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


class PlanityAuth:
    """Encapsulates the 3-step auth chain and token refresh."""

    def __init__(self, email: str, password: str, endpoints: PlanityEndpoints,
                 client: Optional[httpx.AsyncClient] = None):
        self._email = email
        self._password = password
        # The endpoints come from the CALLER (see `config.PlanityEndpoints`):
        # this repo is public and embeds no Planity constants.
        self._endpoints = endpoints
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._tokens: Optional[PlanityTokens] = None

    async def close(self):
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def get_tokens(self) -> PlanityTokens:
        """Get a valid PlanityTokens, refreshing if needed."""
        now = time.time()
        if self._tokens and self._tokens.expires_at - now > 60:
            return self._tokens
        self._tokens = await self._full_login()
        return self._tokens

    async def _full_login(self) -> PlanityTokens:
        """Full 3-step login. Returns enriched tokens."""
        # 1) Firebase email/password login
        r1 = await self._client.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
            f"?key={self._endpoints.firebase_api_key}",
            json={"email": self._email, "password": self._password, "returnSecureToken": True},
            timeout=HTTP_TIMEOUT,
        )
        r1.raise_for_status()
        data1 = r1.json()
        basic_token = data1["idToken"]
        uid = data1["localId"]

        # 2) Planity custom token (enriched with business claims)
        r2 = await self._client.post(
            f"{self._endpoints.rest_api}/getProAuthToken",
            json={
                "uid": uid,
                "token": basic_token,
                "isBusinessSharded": True,
                "isUserSharded": True,
                "source": "web",
            },
            headers={"Origin": "https://pro.planity.com"},
            timeout=HTTP_TIMEOUT,
        )
        r2.raise_for_status()
        custom_token = r2.json()["token"]

        # 3) Exchange for enriched Firebase idToken
        r3 = await self._client.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
            f"?key={self._endpoints.firebase_api_key}",
            json={"token": custom_token, "returnSecureToken": True},
            timeout=HTTP_TIMEOUT,
        )
        r3.raise_for_status()
        data3 = r3.json()
        enriched = data3["idToken"]
        refresh = data3["refreshToken"]
        expires_in = int(data3.get("expiresIn", 3600))

        claims = _decode_jwt_claims(enriched)
        # Business IDs are the claim keys that are not standard JWT fields
        reserved = {
            "iss", "aud", "auth_time", "user_id", "sub", "iat", "exp",
            "email", "email_verified", "firebase", "plPro",
            "isBusinessSharded", "isUserSharded", "source",
        }
        business_ids = [k for k, v in claims.items() if k not in reserved and v == 1]

        return PlanityTokens(
            id_token=enriched,
            refresh_token=refresh,
            uid=uid,
            business_ids=business_ids,
            expires_at=time.time() + expires_in,
        )
