"""Credentials are injected by the consumer — the library never resolves them.

A connector receives every secret (API key, token, client secret…) as a
constructor argument. The library reads no environment variable, no file and no
secret provider to fill a missing one: `require` turns an absent value into a
named `MissingCredential`, so the consumer learns which secret it failed to pass.
"""
from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


class MissingCredential(ValueError):
    """A connector was built without a credential it needs.

    `name` identifies the missing credential (e.g. ``"SERPER_API_KEY"``).
    Subclasses `ValueError`, which callers already catch for bad construction.
    """

    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"Missing credential {name}: the consumer must pass it explicitly "
            f"to the connector (the library never reads secrets from the "
            f"environment, files or secret providers)."
        )


def require(value: T, name: str) -> T:
    """Return `value`, or raise `MissingCredential(name)` when it is absent.

    Absent = falsy (`None`, empty string): an empty secret authenticates nothing.
    """
    if not value:
        raise MissingCredential(name)
    return value
