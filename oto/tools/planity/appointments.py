"""A calendar's appointments: where they live, and how their keys are read.

Three things decide here, and each fails by returning "nothing" rather than an
error — which is why they have their own module:

1. **The address.** Appointments are under `calendar_vevents/<calendar child>`,
   not under `calendars/<calendar>/vevents`: the key is the CHILD (the staff
   member), not the calendar. And they live on the salon's **business shard**
   when it has one — the `calendars-N` database only serves them by default.
2. **The bound.** The `s` index holds `"YYYY-MM-DD HH:MM"`, compared as a STRING:
   an end bound of `"YYYY-MM-DD"` matches nothing at all (every value of the day
   sorts after it) and returns an empty calendar that reads as a day with no
   appointments. The bound is computed here, once (`_fin_de_journee`).
3. **The names.** Storage is abbreviated (`s`, `st`, `d`, `cu`…). The same fields
   carry their FULL name in `calendar_recurring_vevents`: the two nodes
   describe the same object, and `_NOMS` translates that correspondence.

⚠️ **An appointment carries the customer's contact details**: `cu` is a sub-object
`{id, name, email, phone}`. This module is a library and returns what Planity
gives; projection is decided at a tool's boundary, and there it is reduced to the
IDENTIFIER. See the connector note on the backend side.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .firebase_ws import FirebaseRTDB, range_on

#: The one-off appointments node, and the recurring one. Both are
#: indexed by calendar CHILD.
NOEUD_VEVENTS = "calendar_vevents"
NOEUD_RECURRENTS = "calendar_recurring_vevents"

#: The sort index of appointments, and the exact format of its values.
INDEX_JOUR = "s"
_JOUR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEBUT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

#: The abbreviation → full name mapping. It is read from the recurring node,
#: which carries the same fields unabbreviated.
_NOMS = {
    "s": "start",                     # "YYYY-MM-DD HH:MM", salon local time
    "st": "start_minutes",            # minutes since midnight — restates the time of `s`
    "d": "duration_minutes",
    "ca": "calendar_child_id",
    "cu": "customer",                 # {id, name, email, phone} — see the warning
    "cat": "created_at",
    "uat": "updated_at",
    "cby": "created_by",
    "se": "service_id",
    "sq": "sequence",
    "p": "price_cents",
    "seo": "service_origin_id",
    "seoi": "service_origin_index",
    "seop": "service_origin_price_cents",
    "ud": "user_duration_minutes",
    "c": "comment",
    "r": "receipt",                   # {id, periodId} — the till receipt
    "dat": "cancelled_at",
    "dby": "cancelled_by",
    "t": "title",
    "ad": "all_day",
}

#: Abbreviations with no equivalent in the recurring node: they stay
#: under `raw` and get NO name. Naming them by guesswork would pass a
#: hypothesis off as a fact, and it is the kind of name nobody ever revisits.
ABREGES_NON_ELUCIDES = ("rf", "opdm", "bb", "nc", "sca")


def _fin_de_journee(jour: str) -> str:
    """The upper bound that includes the WHOLE day on the `s` index.

    `"2026-09-08"` alone would exclude the entire day: `"2026-09-08 09:30"` sorts
    after it in string comparison. The end of day is therefore written in the
    index's format."""
    return f"{jour} 23:59"


def _exiger_jour(nom: str, valeur: str) -> str:
    if not isinstance(valeur, str) or not _JOUR.match(valeur):
        raise ValueError(
            f"{nom} must be given as a day `YYYY-MM-DD` (received {valeur!r}): the "
            f"appointments index is compared as a string, another form does not "
            f"raise — it returns an empty calendar.")
    return valeur


def _fin(debut: str, minutes: Any) -> Optional[str]:
    """`start` + duration, in the same format and without inventing a timezone.

    Planity stores the salon's WALL-CLOCK time, with no offset. Attaching one here
    would be wrong half the year, and an appointment shifted by an hour goes
    unnoticed — it reads as an appointment."""
    if not _DEBUT.match(debut or "") or not isinstance(minutes, (int, float)):
        return None
    from datetime import datetime, timedelta

    fin = datetime.strptime(debut, "%Y-%m-%d %H:%M") + timedelta(minutes=int(minutes))
    return fin.strftime("%Y-%m-%dT%H:%M")


def traduire(vevent_id: str, child_id: str, brut: dict) -> dict:
    """A stored appointment → its named fields, `raw` included.

    `raw` is kept DELIBERATELY: losing the raw data is what made the service
    catalogue return `price_eur: 0.00` for weeks — the field had changed name,
    and nobody could see it from above any more.
    What `raw` must not do is cross a tool's boundary: it carries the customer
    in clear."""
    if not isinstance(brut, dict):
        raise TypeError(f"an appointment is an object, not {type(brut).__name__}")
    sortie: dict = {"id": vevent_id, "child_id": child_id}
    for abrege, nom in _NOMS.items():
        if abrege in brut:
            sortie[nom] = brut[abrege]
    debut = sortie.get("start")
    sortie["date"] = debut[:10] if isinstance(debut, str) and _DEBUT.match(debut) else None
    sortie["start"] = debut.replace(" ", "T") if isinstance(debut, str) and _DEBUT.match(debut) else debut
    sortie["end"] = _fin(debut if isinstance(debut, str) else "", sortie.get("duration_minutes"))
    client = sortie.get("customer")
    sortie["customer_id"] = client.get("id") if isinstance(client, dict) else None
    ticket = sortie.get("receipt")
    if isinstance(ticket, dict):
        sortie["receipt"] = {"id": ticket.get("id"), "period_id": ticket.get("periodId")}
    # A cancelled appointment has NO "status" field: it has a deletion date.
    # Without this line, a cancelled calendar counts as a full calendar.
    sortie["cancelled"] = sortie.get("cancelled_at") is not None
    sortie["booked_via"] = "website" if brut.get("cby") == "website" else "pro"
    sortie["raw"] = brut
    return sortie


async def lire_jours(db: FirebaseRTDB, child_id: str, jour_debut: str,
                     jour_fin: str) -> list[dict]:
    """The appointments of ONE calendar child between two days, bounds included."""
    _exiger_jour("jour_debut", jour_debut)
    _exiger_jour("jour_fin", jour_fin)
    if jour_fin < jour_debut:
        raise ValueError(f"reversed window: {jour_debut} → {jour_fin}")
    brut = await db.get(
        f"{NOEUD_VEVENTS}/{child_id}",
        range_on(INDEX_JOUR, jour_debut, _fin_de_journee(jour_fin)))
    if not isinstance(brut, dict):
        return []
    return [traduire(vid, child_id, v) for vid, v in brut.items() if isinstance(v, dict)]


async def lire_un(db: FirebaseRTDB, child_id: str, vevent_id: str) -> Optional[dict]:
    """A specific appointment, or `None` if it is not in this calendar."""
    brut = await db.get(f"{NOEUD_VEVENTS}/{child_id}/{vevent_id}")
    # An unknown identifier returns `{}`, not `None`: without this test, it would
    # come out translated as an appointment with NO date or customer — an object
    # mistaken for a badly filled appointment, when it does not exist.
    if not isinstance(brut, dict) or not brut:
        return None
    return traduire(vevent_id, child_id, brut)


async def lire_recurrents(db: FirebaseRTDB, child_id: str,
                          limite: int = 100) -> list[dict]:
    """The RECURRING appointments of a calendar child.

    They are not in `calendar_vevents` and therefore appear in no per-day read:
    a calendar that only has recurrences reads as an empty calendar. Their fields
    already carry their full name — this node served as the dictionary for the
    other's abbreviations."""
    from .firebase_ws import limit_last

    brut = await db.get(f"{NOEUD_RECURRENTS}/{child_id}", limit_last(limite))
    if not isinstance(brut, dict):
        return []
    sortie = []
    for rid, r in brut.items():
        if not isinstance(r, dict):
            continue
        client = r.get("customer")
        sortie.append({
            "id": rid,
            "child_id": child_id,
            "rrule": r.get("rrule"),
            "duration_minutes": r.get("duration"),
            "user_duration_minutes": r.get("userDuration"),
            "service_id": r.get("service"),
            "sequence": r.get("sequence"),
            "price_cents": r.get("price"),
            "customer_id": client.get("id") if isinstance(client, dict) else None,
            "customer": client,
            "created_at": r.get("createdAt"),
            "created_by": r.get("createdBy"),
            "updated_at": r.get("updatedAt"),
            "all_day": r.get("allDay"),
            "title": r.get("title"),
            "comment": r.get("comment"),
            "raw": r,
        })
    return sortie
