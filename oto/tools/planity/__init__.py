"""Planity client (a salon's calendar + till) — READ-ONLY.

`PlanityClient` is the entry point: it authenticates with the account's email and
password, keeps its token fresh, and serves reference data, customers, the
calendar and the figures. The neighbouring modules each carry one transport; that
is an implementation detail, not a surface.

Everything is **asynchronous**, and that is not a style choice: the upstream
imposes it, and there is no synchronous equivalent to write.

⚠️ **Planity's endpoints are NOT here.** `PlanityClient` requires a
`PlanityEndpoints` (Firebase API key, App ID, root of the REST lambdas), with no
default value: this repo is public, a client published here describes a
protocol and does not hard-code a third-party company's constants. They
are public by design — any browser that opens `pro.planity.com` receives
them — so taking them out of here is not a gesture of secrecy, it is one of
genericity: whoever deploys the connector sets them, and answers for what they
call.

No write is exposed: no creation or modification of appointments.
"""
from __future__ import annotations

#: The modules that the `planity` extra brings, and nothing else. Named here because
#: this is the only place that knows WHY they are missing.
_MODULES_DE_L_EXTRA = ("httpx", "websockets")


def _refus_d_extra(e: ImportError) -> "ImportError | None":
    """The error to raise INSTEAD when it is the extra that is missing — otherwise `None`.

    Without it, an installer without the extra returns "No module named 'httpx'". That
    message is true and perfectly useless: it has never made anyone install an
    extra, and it does not say that only the connector is affected.
    At the consumer (oto-backend), it becomes a log line
    "planity tools disabled: No module named 'httpx'" on which one hunts for an
    import bug for twenty minutes.

    The retranslation lives HERE, at the origin, and not at the consumer: there are
    several of them (oto-backend, an installer that tries), and a rule
    set at one does not protect the others.

    ⚠️ It applies ONLY to the extra's two modules. An internal `ImportError`
    — a renamed package module, a circular import — must bubble up as is:
    disguising it as "install the extra" would send people looking for the fault
    in the opposite direction from where it is.
    """
    if getattr(e, "name", None) not in _MODULES_DE_L_EXTRA:
        return None
    return ImportError(
        f"the `planity` connector needs the extra of the same name — install "
        f"`oto-core[planity]` (`{e.name}` is missing). The Planity core speaks the "
        f"WebSocket protocol of Firebase's Realtime Database and makes its calls "
        f"asynchronously: neither exists in `requests`, the foundation of the "
        f"rest of the lib — hence an extra rather than a dependency for everyone.")


try:
    from . import appointments, pos, services, stock
    from .auth import PlanityAuth, PlanityTokens
    from .client import Employee, PlanityClient, SalonInfo
    from .config import PlanityEndpoints
    from .date_range import ms_to_iso, resolve_range
except ImportError as _e:
    _refus = _refus_d_extra(_e)
    if _refus is None:
        raise
    raise _refus from _e

__all__ = [
    "Employee",
    "appointments",
    "pos",
    "services",
    "stock",
    "PlanityAuth",
    "PlanityClient",
    "PlanityEndpoints",
    "PlanityTokens",
    "SalonInfo",
    "ms_to_iso",
    "resolve_range",
]
