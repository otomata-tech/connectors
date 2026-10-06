"""Monid — paid gateway to ~2,000 data endpoints (~70 providers),
billed per call against a prepaid wallet: find, inspect, launch, follow."""

from .client import (
    DEFAULT_BASE_URL,
    RUN_READ_TIMEOUT,
    RUN_STATUSES,
    SERVICE,
    TERMINAL_STATUSES,
    MonidClient,
    MonidHTTPError,
    MonidProtocolError,
    is_terminal,
    run_cost_usd,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "RUN_READ_TIMEOUT",
    "RUN_STATUSES",
    "SERVICE",
    "TERMINAL_STATUSES",
    "MonidClient",
    "MonidHTTPError",
    "MonidProtocolError",
    "is_terminal",
    "run_cost_usd",
]
