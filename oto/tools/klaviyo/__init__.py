"""Klaviyo API client (private key): CRM reads and writes, no campaign sending."""

from .client import REVISION, KlaviyoClient, next_cursor
from .errors import KlaviyoError, KlaviyoPaginationError, KlaviyoRateLimited
from .rate_limit import ENDPOINT_LIMITS, EndpointRateLimiter

__all__ = [
    "ENDPOINT_LIMITS",
    "EndpointRateLimiter",
    "KlaviyoClient",
    "KlaviyoError",
    "KlaviyoPaginationError",
    "KlaviyoRateLimited",
    "REVISION",
    "next_cursor",
]
