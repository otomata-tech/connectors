"""The service catalogue, and its price.

A service has no `price` field. It has `prices`, an object with three mutually
exclusive shapes:

- `{"default": <cents>}` — a firm price;
- `{"min": <cents>, "max": <cents>}` — a range (colouring, hair length…);
- `{"onQuotation": true}` — on quotation.

And it may have none at all: `prices` is missing from one service in two.

That is the bug that returned `price_eur: 0.00` on the WHOLE catalogue: the reader
looked for `price`, never found it, and returned zero. A zero raises nothing —
it reads as a free service, and it was for weeks. Hence the shape returned here:
a `kind` that SAYS which of the four situations we have, rather than a number
that cannot say it does not exist.
"""
from __future__ import annotations

from typing import Optional

#: What `prix` returns in `kind`.
FIXE = "fixed"
FOURCHETTE = "range"
SUR_DEVIS = "on_quotation"
ABSENT = "unpriced"


def prix(prestation: dict) -> dict:
    """A service's price, in CENTS, with the shape it actually has."""
    brut = (prestation or {}).get("prices")
    if not isinstance(brut, dict) or not brut:
        return {"kind": ABSENT, "default_cents": None,
                "min_cents": None, "max_cents": None}
    if brut.get("onQuotation"):
        return {"kind": SUR_DEVIS, "default_cents": None,
                "min_cents": None, "max_cents": None}
    defaut = _centimes(brut.get("default"))
    if defaut is not None:
        return {"kind": FIXE, "default_cents": defaut,
                "min_cents": defaut, "max_cents": defaut}
    mini, maxi = _centimes(brut.get("min")), _centimes(brut.get("max"))
    if mini is not None or maxi is not None:
        return {"kind": FOURCHETTE, "default_cents": None,
                "min_cents": mini, "max_cents": maxi}
    return {"kind": ABSENT, "default_cents": None, "min_cents": None, "max_cents": None}


def _centimes(v) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def aplatir(catalogue: dict) -> list[dict]:
    """The catalogue `{group: {children: {service}}}` flattened, prices included.

    Deletion applies at BOTH levels: a live service inside a deleted group is no
    longer offered. The two dates are returned separately, and `deleted` says what
    matters — without it, a stale catalogue presents itself as an offer."""
    sortie = []
    for gid, groupe in (catalogue or {}).items():
        if not isinstance(groupe, dict):
            continue
        enfants = groupe.get("children") or {}
        if not isinstance(enfants, dict):
            continue
        groupe_supprime = groupe.get("deletedAt")
        for sid, s in enfants.items():
            if not isinstance(s, dict):
                continue
            supprime = s.get("deletedAt")
            sortie.append({
                "id": sid,
                "category_id": gid,
                "category_name": (groupe.get("name") or "").strip(),
                "name": (s.get("name") or "").strip(),
                "duration_minutes": s.get("duration"),
                "bookable": s.get("bookable", True),
                "description": (s.get("description") or "")[:300],
                "sequence": s.get("sequence"),
                "deleted_at": supprime,
                "category_deleted_at": groupe_supprime,
                "deleted": supprime is not None or groupe_supprime is not None,
                "prices": prix(s),
            })
    return sortie
