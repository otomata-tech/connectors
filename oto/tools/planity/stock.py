"""Stock: purchase lots, movements, mass removals.

A product's stock is not a number — it is a MAP of purchase lots
(`stocks`), each with its remaining quantity and its purchase price. Summing the
quantities gives the stock; ignoring the lots makes the purchase price vanish, and
with it the margin.

Movements live under `business_stock_movements/<salon>/<product>`: one
movement node PER PRODUCT, thousands in all. No global read holds up —
we read product by product, over a `createdAt` range.

⚠️ **The product list is taken from the CATALOGUE, not from the movements node.**
The latter is read bounded, and a bound silently truncates there: on a salon with nine
hundred products, the last ones simply have no movement — which reads
as "nothing sold" and skews every order forecast.
"""
from __future__ import annotations

from typing import Iterable

from .firebase_ws import FirebaseRTDB, limit_last, range_on

NOEUD_MOUVEMENTS = "business_stock_movements"
NOEUD_SORTIES_DE_MASSE = "business_mass_removed_stocks"
NOEUD_PRODUITS = "business_products"

INDEX_MOUVEMENT = "createdAt"

#: The movement types of the model. `sale` carries consumption,
#: `saleCancellation` takes it back. A type outside this list is NOT filtered:
#: it documents, it does not decide.
TYPES_CONNUS = ("creation", "sale", "update", "saleCancellation")


def produit(product_id: str, category_id: str, brut: dict) -> dict:
    """A catalogue product, its lots and its thresholds.

    `stock_threshold` / `stock_ceiling` / `supplier_id` exist in Planity's model
    and may be filled in nowhere: `None` means "the salon does not use it", not
    "zero". An order forecast that read `0` as a threshold would order everything,
    all the time."""
    lots_bruts = brut.get("stocks") or {}
    lots = []
    quantite = 0
    if isinstance(lots_bruts, dict):
        for lot_id, lot in lots_bruts.items():
            if not isinstance(lot, dict):
                continue
            q = int(lot.get("quantity") or 0)
            quantite += q
            lots.append({
                "id": lot_id,
                "quantity": q,
                "purchase_price_cents": lot.get("purchasePrice"),
                "initial_purchase_price_cents": lot.get("initialPurchasePrice"),
                "created_at": lot.get("createdAt"),
                "updated_at": lot.get("updatedAt"),
            })
    lots.sort(key=lambda l: l.get("created_at") or 0)
    return {
        "id": product_id,
        "category_id": category_id,
        "name": (brut.get("name") or "").strip(),
        "price_cents": brut.get("price"),
        "ean": brut.get("eanCode"),
        "brand": brut.get("brand"),
        "stock_total": quantite,
        "stock_lots": lots,
        "stock_threshold": brut.get("stockThreshold"),
        "stock_ceiling": brut.get("stockCeiling"),
        "supplier_id": brut.get("supplierId"),
        "click_and_collect": brut.get("clickAndCollect"),
        "deleted_at": brut.get("deletedAt"),
        "deleted": brut.get("deletedAt") is not None,
    }


def aplatir_produits(catalogue: dict) -> list[dict]:
    """The catalogue `{category: {children: {product}}}` flattened."""
    sortie = []
    for cat_id, cat in (catalogue or {}).items():
        if not isinstance(cat, dict):
            continue
        enfants = cat.get("children") or {}
        if not isinstance(enfants, dict):
            continue
        for pid, p in enfants.items():
            if isinstance(p, dict):
                sortie.append(produit(pid, cat_id, p))
    return sortie


def mouvement(product_id: str, mov_id: str, brut: dict) -> dict:
    """A named movement.

    ⚠️ **`purchasePrice` is not always a number**: it is also the string
    `"any"` (a removal that targets no purchase lot in particular). Converting it
    to euros without looking raises at the first movement of this kind — in the middle of
    an otherwise good read. So we separate the two: `purchase_price_cents`
    is the amount WHEN it is one, `purchase_price_raw` is what the upstream
    wrote. A `None` in cents means "not an amount", not "free"."""
    achat = brut.get("purchasePrice")
    montant = achat if isinstance(achat, (int, float)) and not isinstance(achat, bool) else None
    return {
        "id": mov_id,
        "product_id": product_id,
        "created_at": brut.get("createdAt"),
        "quantity": brut.get("quantity"),
        "type": brut.get("type"),
        "purchase_price_cents": montant,
        "purchase_price_raw": achat,
        "child_stock_id": brut.get("childStockId"),
        "motive": brut.get("motive"),
    }


async def lire_mouvements(db: FirebaseRTDB, business_id: str,
                          product_ids: Iterable[str], gte_ms: int,
                          lte_ms: int) -> list[dict]:
    """The movements of these products in the window — one bounded read per product.

    `product_ids` is EXPLICIT and has no default: no global read of movements
    holds up (thousands, on a node with no time index at salon level). A caller
    who wants the whole catalogue says so by passing the whole catalogue, and then
    knows what it is paying."""
    sortie: list[dict] = []
    for pid in product_ids:
        brut = await db.get(f"{NOEUD_MOUVEMENTS}/{business_id}/{pid}",
                            range_on(INDEX_MOUVEMENT, gte_ms, lte_ms))
        if not isinstance(brut, dict):
            continue
        sortie += [mouvement(pid, mid, m) for mid, m in brut.items()
                   if isinstance(m, dict)]
    sortie.sort(key=lambda m: m.get("created_at") or 0)
    return sortie


async def lire_sorties_de_masse(db: FirebaseRTDB, business_id: str,
                                limite: int = 50) -> list[dict]:
    """Grouped stock removals (inventory, breakage, expiry)."""
    brut = await db.get(f"{NOEUD_SORTIES_DE_MASSE}/{business_id}", limit_last(limite))
    if not isinstance(brut, dict):
        return []
    sorties = []
    for sid, s in brut.items():
        if not isinstance(s, dict):
            continue
        produits = s.get("products") or {}
        sorties.append({
            "id": sid,
            "created_at": s.get("createdAt"),
            "products_count": len(produits) if isinstance(produits, (dict, list)) else 0,
            "products": produits,
        })
    sorties.sort(key=lambda s: s.get("created_at") or 0)
    return sorties

