"""PlanityClient — single entry point orchestrating auth + the three API layers.

Caches:
- auth tokens (refreshed proactively)
- business metadata (after first lookup)
- Algolia credentials per business
- Open Firebase RTDB WebSockets (master + per-shard)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx

from . import appointments as _rdv
from . import pos as _pos
from . import stock as _stock
from .algolia import AlgoliaClient
from .auth import PlanityAuth
from .config import PlanityEndpoints
from .firebase_ws import FirebaseRTDB, calendar_shard_index
from .rest_api import PlanityREST


@dataclass
class Employee:
    """A calendar CHILD. Often a staff member — not always.

    `deleted_at` is set when the child was deleted on the Planity side: its
    calendar remains readable (past appointments are in it), but it no longer
    counts in the team. Mistaking it for an active one makes a salon with three
    staff members announce seven.

    `type` and `title` distinguish a child that is NOT a person — a booth, a
    workstation, a resource. They are returned as is: they are the upstream's
    values, and giving them a meaning here would invent one."""

    id: str
    name: str
    color: Optional[str] = None
    picture: Optional[str] = None
    calendar_id: Optional[str] = None
    deleted_at: Optional[int] = None
    type: Optional[str] = None
    title: Optional[str] = None

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass
class SalonInfo:
    id: str
    name: str
    slug: str
    phone: Optional[str]
    db_shard: str
    calendars: list[str] = field(default_factory=list)
    opening_hours: Optional[str] = None
    employees: list[Employee] = field(default_factory=list)


class PlanityClient:
    def __init__(self, email: str, password: str, endpoints: PlanityEndpoints):
        # `endpoints` is REQUIRED and has no default: this repo is public and
        # carries no Planity endpoints (see `config.PlanityEndpoints`).
        # Whoever deploys the connector sets them, and answers for what they call.
        self._endpoints = endpoints
        self._http = httpx.AsyncClient(timeout=30.0)
        self.auth = PlanityAuth(email, password, endpoints, client=self._http)
        self.rest = PlanityREST(endpoints, client=self._http)
        self.algolia = AlgoliaClient(endpoints, client=self._http)

        self._salons: dict[str, SalonInfo] = {}
        self._master: Optional[FirebaseRTDB] = None
        self._shards: dict[str, FirebaseRTDB] = {}
        self._current_token: Optional[str] = None
        self._lock = asyncio.Lock()

    async def close(self):
        for db in list(self._shards.values()):
            await db.close()
        if self._master:
            await self._master.close()
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ─── connection plumbing ───

    async def _ensure_master(self) -> FirebaseRTDB:
        async with self._lock:
            tokens = await self.auth.get_tokens()
            if self._master and self._current_token == tokens.id_token:
                return self._master
            if self._master:
                await self._master.close()
            for db in self._shards.values():
                await db.close()
            self._shards.clear()
            self._master = FirebaseRTDB.master(
                tokens.id_token, self._endpoints.firebase_app_id)
            await self._master.connect()
            self._current_token = tokens.id_token
            return self._master

    async def _ensure_shard(self, shard_name: str) -> FirebaseRTDB:
        async with self._lock:
            tokens = await self.auth.get_tokens()
            if shard_name in self._shards and self._current_token == tokens.id_token:
                return self._shards[shard_name]
            if shard_name in self._shards:
                await self._shards[shard_name].close()
            db = FirebaseRTDB.business_shard(
                shard_name, tokens.id_token, self._endpoints.firebase_app_id)
            await db.connect()
            self._shards[shard_name] = db
            return db

    async def _ensure_calendars_shard(self, child_id: str) -> FirebaseRTDB:
        """The `calendars-N` database of this calendar child, kept open.

        It is indexed by a COMPUTATION on the identifier, not by a lookup — and
        it only serves salons WITHOUT a business shard (`_agenda_db`)."""
        cle = f"calendars-{calendar_shard_index(child_id)}"
        async with self._lock:
            tokens = await self.auth.get_tokens()
            if cle in self._shards and self._current_token == tokens.id_token:
                return self._shards[cle]
            if cle in self._shards:
                await self._shards[cle].close()
            db = FirebaseRTDB.calendars_shard(
                child_id, tokens.id_token, self._endpoints.firebase_app_id)
            await db.connect()
            self._shards[cle] = db
            return db

    async def _agenda_db(self, salon: SalonInfo, child_id: str) -> FirebaseRTDB:
        """Where this salon's appointments live.

        On the **business shard** as soon as it has one; the `calendars-N` database
        only serves them by default. Targeting the wrong one returns an empty node,
        never a refusal: the calendar then reads as a calendar with no appointments."""
        if salon.db_shard and salon.db_shard != "master":
            return await self._ensure_shard(salon.db_shard)
        return await self._ensure_calendars_shard(child_id)

    async def _enfants_dagenda(self, salon_id: str,
                               employee_id: Optional[str] = None) -> tuple[SalonInfo, list[str]]:
        """The salon and the calendar children to read — all, or the one requested.

        DELETED children are read like the others: their calendar keeps the past
        appointments, and discarding them would make a departed staff member
        vanish from the history — less revenue with nothing to flag it."""
        salon = await self.get_salon(salon_id)
        ids = [e.id for e in salon.employees]
        if employee_id is None:
            return salon, ids
        if employee_id not in ids:
            # Reading a child that does not belong to this salon would return an empty
            # calendar, and a typo would read as "this staff member has nothing".
            raise ValueError(
                f"{employee_id} is not a calendar of this salon — "
                f"`list_employees` gives the identifiers that are.")
        return salon, [employee_id]

    # ─── Reference data ───

    async def list_salons(self) -> list[SalonInfo]:
        tokens = await self.auth.get_tokens()
        master = await self._ensure_master()
        results: list[SalonInfo] = []
        for bid in tokens.business_ids:
            if bid in self._salons:
                results.append(self._salons[bid])
                continue
            name = await master.get(f"businesses/{bid}/name") or "?"
            slug = await master.get(f"businesses/{bid}/slug") or ""
            phone_raw = await master.get(f"businesses/{bid}/phoneNumber")
            phone = phone_raw if isinstance(phone_raw, str) else None
            db_shard = await master.get(f"businesses/{bid}/db") or "master"
            opening = await master.get(f"businesses/{bid}/openingHours")
            calendars = await master.get(f"businesses/{bid}/calendars")
            cal_ids: list[str] = []
            employees: list[Employee] = []
            if isinstance(calendars, dict):
                for cid, cdata in calendars.items():
                    cal_ids.append(cid)
                    children = (cdata or {}).get("children", {}) if isinstance(cdata, dict) else {}
                    if isinstance(children, dict):
                        for child_id, child in children.items():
                            if not isinstance(child, dict):
                                continue
                            employees.append(Employee(
                                id=child_id,
                                name=(child.get("name") or "").strip(),
                                color=child.get("color"),
                                picture=child.get("picture"),
                                calendar_id=cid,
                                deleted_at=child.get("deletedAt"),
                                type=child.get("type"),
                                title=child.get("title"),
                            ))
            info = SalonInfo(
                id=bid, name=name, slug=slug, phone=phone, db_shard=db_shard,
                calendars=cal_ids, opening_hours=opening if isinstance(opening, str) else None,
                employees=employees,
            )
            self._salons[bid] = info
            results.append(info)
        return results

    async def get_salon(self, salon_id: str) -> SalonInfo:
        if salon_id in self._salons:
            return self._salons[salon_id]
        await self.list_salons()
        if salon_id not in self._salons:
            raise ValueError(f"Salon {salon_id} not accessible")
        return self._salons[salon_id]

    async def list_services(self, salon_id: str) -> dict[str, dict]:
        """Service groups from master. Structure: {groupId: {children: {childId: svc}, ...}}."""
        await self.get_salon(salon_id)
        master = await self._ensure_master()
        data = await master.get(f"businesses/{salon_id}/services")
        return data if isinstance(data, dict) else {}

    async def list_products(self, salon_id: str) -> dict[str, dict]:
        """Product categories from business shard. Structure mirrors services
        ({categoryId: {children: {productId: product}}})."""
        salon = await self.get_salon(salon_id)
        shard = await self._ensure_shard(salon.db_shard)
        data = await shard.get(f"business_products/{salon_id}")
        return data if isinstance(data, dict) else {}

    # ─── Customers ───

    async def search_customers(self, salon_id: str, query: str = "", limit: int = 10) -> list[dict]:
        tokens = await self.auth.get_tokens()
        creds = await self.algolia.get_credentials(tokens.id_token, salon_id)
        resp = await self.algolia.search_customers(creds, query, hits_per_page=limit)
        return resp.get("hits", [])

    async def get_customer(self, salon_id: str, customer_id: str) -> dict:
        salon = await self.get_salon(salon_id)
        shard = await self._ensure_shard(salon.db_shard)
        data = await shard.get(f"business_customers/{salon_id}/{customer_id}")
        return data or {}

    async def get_customer_stats(self, salon_id: str, customer_id: str) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_customer_stats(salon_id, customer_id, tokens.id_token)

    async def get_customer_receipts(self, salon_id: str, customer_id: str) -> list[dict]:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_customer_receipts(salon_id, customer_id, tokens.id_token)

    # ─── Planning / Appointments ───

    async def list_appointments(self, salon_id: str, day_from: str, day_to: str,
                                employee_id: Optional[str] = None) -> list[dict]:
        """The salon's appointments between two DAYS (`YYYY-MM-DD`), bounds included.

        The window is in days and not timestamps: Planity's sort index holds
        `"YYYY-MM-DD HH:MM"` in wall-clock time, with no offset — converting it to
        milliseconds would gain or lose an hour at both ends depending on the
        season, and one appointment more or less goes unnoticed.

        A CANCELLED appointment is returned like the others, with `cancelled=True`:
        there is no "status" field at Planity, only a deletion date, and filtering
        it out by default would hide cancellations from whoever looks for them."""
        salon, enfants = await self._enfants_dagenda(salon_id, employee_id)
        sortie: list[dict] = []
        for child_id in enfants:
            db = await self._agenda_db(salon, child_id)
            sortie += await _rdv.lire_jours(db, child_id, day_from, day_to)
        sortie.sort(key=lambda v: (v.get("start") or "", v.get("child_id") or ""))
        return sortie

    async def get_appointment(self, salon_id: str, vevent_id: str,
                              employee_id: Optional[str] = None) -> Optional[dict]:
        """An appointment by its identifier.

        Without `employee_id`, the salon's calendars are scanned until it is found:
        an appointment identifier does not say which calendar it comes from, and
        the caller does not always have that."""
        salon, enfants = await self._enfants_dagenda(salon_id, employee_id)
        for child_id in enfants:
            db = await self._agenda_db(salon, child_id)
            trouve = await _rdv.lire_un(db, child_id, vevent_id)
            if trouve is not None:
                return trouve
        return None

    async def list_recurring_appointments(self, salon_id: str,
                                          employee_id: Optional[str] = None,
                                          limit: int = 100) -> list[dict]:
        """Recurring appointments — invisible to any per-day read."""
        salon, enfants = await self._enfants_dagenda(salon_id, employee_id)
        sortie: list[dict] = []
        for child_id in enfants:
            db = await self._agenda_db(salon, child_id)
            sortie += await _rdv.lire_recurrents(db, child_id, limit)
        return sortie

    # ─── Till ───

    async def list_pos_periods(self, salon_id: str, gte_ms: int, lte_ms: int,
                               limit: Optional[int] = None) -> list[dict]:
        """The till sessions in the window, without their receipts."""
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _pos.lire_periodes(db, salon_id, gte_ms, lte_ms, limit)

    async def get_pos_period(self, salon_id: str, period_id: str) -> Optional[dict]:
        """A till session WITH its receipts."""
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _pos.lire_periode(db, salon_id, period_id)

    async def get_receipt(self, salon_id: str, period_id: str,
                          receipt_id: str) -> Optional[dict]:
        """A receipt. It lives under its period — it has no address of its own."""
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _pos.lire_ticket(db, salon_id, period_id, receipt_id)

    async def list_payment_methods(self, salon_id: str) -> list[dict]:
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _pos.lire_moyens_paiement(db, salon_id)

    # ─── Stock ───

    async def list_stock_movements(self, salon_id: str, product_ids: list[str],
                                   gte_ms: int, lte_ms: int) -> list[dict]:
        """The stock movements of these products, one bounded read per product."""
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _stock.lire_mouvements(db, salon_id, product_ids, gte_ms, lte_ms)

    async def list_mass_stock_removals(self, salon_id: str, limit: int = 50) -> list[dict]:
        salon = await self.get_salon(salon_id)
        db = await self._ensure_shard(salon.db_shard)
        return await _stock.lire_sorties_de_masse(db, salon_id, limit)

    async def list_suppliers(self, salon_id: str) -> list[dict]:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_products_suppliers(salon_id, tokens.id_token)

    async def list_product_orders(self, salon_id: str,
                                  cursor: Optional[str] = None) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_products_orders(salon_id, tokens.id_token, cursor)

    # ─── Business stats ───

    async def get_key_indicators(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_key_indicators(salon_id, tokens.id_token, gte_ms, lte_ms)

    async def get_revenues(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_revenues(salon_id, tokens.id_token, gte_ms, lte_ms)

    async def get_best_customers(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_best_customers(salon_id, tokens.id_token, gte_ms, lte_ms)

    async def get_new_customers(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_new_customers(salon_id, tokens.id_token, gte_ms, lte_ms)

    async def get_overall_frequencies(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        tokens = await self.auth.get_tokens()
        return await self.rest.get_overall_frequencies(salon_id, tokens.id_token, gte_ms, lte_ms)

    async def _seller_and_calendar_ids(self, salon_id: str) -> tuple[list[str], list[str]]:
        salon = await self.get_salon(salon_id)
        return [e.id for e in salon.employees], salon.calendars

    async def get_revenue_breakdown(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_revenue_breakdown(salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_calendar_stats(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_calendar_stats(salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_occupancy_rate(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_occupancy_rate(salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_reviews_stats(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_reviews_stats(salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_revenue_by_payment_method(self, salon_id: str, gte_ms: int,
                                            lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_revenue_by_payment_method(
            salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_revenue_by_vat(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_revenue_by_vat(
            salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)

    async def get_service_stats(self, salon_id: str, gte_ms: int, lte_ms: int) -> dict:
        seller_ids, cal_ids = await self._seller_and_calendar_ids(salon_id)
        tokens = await self.auth.get_tokens()
        return await self.rest.get_service_stats(
            salon_id, tokens.id_token, gte_ms, lte_ms,
            seller_ids=seller_ids, calendar_ids=cal_ids)
