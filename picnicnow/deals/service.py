"""Vergelijk duurdere, vaak gekochte Picnic-producten met aanbiedingen bij AH, Vomar en DekaMarkt."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import Settings
from ..db import Database, now_iso
from ..shopping import parse_amount
from ..text import coverage, is_organic, normalize, same_variant, similarity
from .base import Deal, DealProvider, effective_price

log = logging.getLogger(__name__)

STORE_NAMES = {"ah": "Albert Heijn", "vomar": "Vomar", "dekamarkt": "DekaMarkt"}
MIN_SAVING_CENTS = 30
MIN_SAVING_SHARE = 0.10


def make_providers(settings: Settings) -> list[DealProvider]:
    from .ah import AlbertHeijn
    from .stores import DekaMarkt, Vomar

    factories = {"ah": AlbertHeijn, "vomar": Vomar, "dekamarkt": lambda: DekaMarkt(settings)}
    return [factories[s]() for s in settings.deal_stores if s in factories]


def watchlist(db: Database, settings: Settings, limit: int = 25) -> list[dict]:
    """Duurdere producten die jullie regelmatig bij Picnic kopen (op volgorde van uitgaven)."""
    rows = db.query(
        "SELECT p.id, p.name, p.unit_quantity, p.price_cents, p.is_organic, COUNT(*) AS times, "
        "AVG(pu.quantity) AS avg_q FROM purchases pu JOIN products p ON p.id = pu.product_id "
        "WHERE p.price_cents >= ? GROUP BY p.id HAVING COUNT(*) >= 2 "
        "ORDER BY p.price_cents * COUNT(*) DESC LIMIT ?",
        (settings.deal_min_price_cents, limit),
    )
    return rows


_NOISE = re.compile(r"\b(picnic|verse?|eetrijp|heel|plakken|naturel|msc|\d+[.,]?\d*\s*(g|kg|ml|l|stuks?))\b")


def search_term(name: str) -> str:
    n = normalize(name)
    n = _NOISE.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip() or normalize(name)


def refresh_deals(db: Database, settings: Settings, providers: list[DealProvider] | None = None) -> dict:
    providers = providers if providers is not None else make_providers(settings)
    terms = [search_term(w["name"]) for w in watchlist(db, settings)]
    result = {}
    for provider in providers:
        try:
            deals = provider.fetch(terms)
        except Exception as exc:  # een bron die stuk is mag de rest niet blokkeren
            log.warning("Aanbiedingen %s mislukt: %s", provider.store, exc)
            result[provider.store] = {"ok": False, "error": str(exc)}
            continue
        with db.tx() as conn:
            conn.execute("DELETE FROM deals WHERE store = ?", (provider.store,))
            conn.executemany(
                "INSERT INTO deals(store, title, price_cents, original_price_cents, label, unit, valid_from, "
                "valid_until, url, is_organic, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(d.store, d.title, d.price_cents, d.original_price_cents, d.label, d.unit, d.valid_from,
                  d.valid_until, d.url, int(d.is_organic), now_iso()) for d in deals],
            )
        result[provider.store] = {"ok": True, "deals": len(deals)}
    db.set_pref("deals_refreshed", now_iso())
    return result


def _unit_price(price_cents: int | None, unit: str | None) -> tuple[float, str] | None:
    amount = parse_amount(unit)
    if not price_cents or not amount or amount[0] <= 0:
        return None
    return price_cents / amount[0], amount[1]


def compare(picnic_price: int, picnic_unit: str | None, other_price: int, other_unit: str | None) -> dict:
    """Besparing per Picnic-verpakking; per kilo/liter/stuk als de groottes bekend zijn."""
    a, b = _unit_price(picnic_price, picnic_unit), _unit_price(other_price, other_unit)
    if a and b and a[1] == b[1]:
        amount = parse_amount(picnic_unit)[0]
        equivalent = round(b[0] * amount)
        return {"saving_cents": picnic_price - equivalent, "equivalent_cents": equivalent, "per_unit": True}
    return {"saving_cents": picnic_price - other_price, "equivalent_cents": other_price, "per_unit": False}


def _deal_matches(product: dict, deals: list[dict], organic_level: int) -> list[dict]:
    out = []
    want_organic = bool(product["is_organic"])
    for d in deals:
        term = search_term(product["name"])
        s = similarity(term, d["title"])
        if s < 0.6 or coverage(d["title"], term) < 0.4 or not same_variant(product["name"], d["title"]):
            continue
        if want_organic and not d["is_organic"] and organic_level >= 2:
            continue
        deal = Deal(**{k: d[k] for k in ("store", "title", "price_cents", "original_price_cents", "label", "unit",
                                         "valid_from", "valid_until", "url")}, is_organic=bool(d["is_organic"]))
        price = effective_price(deal)
        if not price:
            continue
        cmp = compare(product["price_cents"], product["unit_quantity"], price, deal.unit or deal.title)
        out.append({"deal": deal.to_dict(), "match": s, "effective_cents": price, **cmp,
                    "organic_mismatch": want_organic and not deal.is_organic})
    return out


def opportunities(db: Database, settings: Settings, checkjebon=None, organic_level: int | None = None) -> list[dict]:
    """Waar kun je deze week (of structureel) goedkoper uit zijn dan bij Picnic?"""
    organic_level = settings.organic_level if organic_level is None else organic_level
    deals = db.query("SELECT * FROM deals")
    by_store: dict[str, list[dict]] = {}
    for d in deals:
        by_store.setdefault(d["store"], []).append(d)

    results = []
    for product in watchlist(db, settings):
        best = None
        for store, store_deals in by_store.items():
            for m in _deal_matches(product, store_deals, organic_level):
                saving = m["saving_cents"]
                if saving < MIN_SAVING_CENTS or saving < MIN_SAVING_SHARE * product["price_cents"]:
                    continue
                cand = {"type": "aanbieding", "store": store, "store_name": STORE_NAMES.get(store, store), **m}
                if best is None or (cand["saving_cents"] - (50 if cand["organic_mismatch"] else 0)) > \
                        (best["saving_cents"] - (50 if best["organic_mismatch"] else 0)):
                    best = cand
        if best is None and checkjebon is not None:
            best = _regular_price_tip(product, settings, checkjebon, organic_level)
        if best:
            per_week = best["saving_cents"] * max(1, round(product["avg_q"] or 1)) * min(1.0, product["times"] / 4)
            results.append({"product": {k: product[k] for k in ("id", "name", "unit_quantity", "price_cents",
                                                                   "is_organic", "times")},
                            **best, "expected_saving_cents": round(per_week)})
    results.sort(key=lambda r: -r["expected_saving_cents"])
    return results


def _regular_price_tip(product: dict, settings: Settings, checkjebon, organic_level: int) -> dict | None:
    """Structureel goedkoper (geen actie) volgens Checkjebon – alleen bij flink verschil."""
    best = None
    term = search_term(product["name"])
    for store in settings.deal_stores:
        for item in checkjebon.prices(store):
            name = item.get("n") or ""
            if bool(product["is_organic"]) != is_organic(name):
                continue
            s = similarity(term, name)
            if s < 0.75 or coverage(name, term) < 0.5 or not same_variant(product["name"], name):
                continue
            price = round((item.get("p") or 0) * 100)
            if not price:
                continue
            cmp = compare(product["price_cents"], product["unit_quantity"], price, item.get("s"))
            if not cmp["per_unit"]:
                continue  # zonder grootte is een vergelijking met reguliere prijzen te onzeker
            if cmp["saving_cents"] < max(MIN_SAVING_CENTS, 0.15 * product["price_cents"]):
                continue
            cand = {"type": "regulier", "store": store, "store_name": STORE_NAMES.get(store, store),
                    "deal": {"title": name, "price_cents": price, "unit": item.get("s"),
                             "url": checkjebon.url(store, item.get("l")), "label": "vaste prijs"},
                    "match": s, "effective_cents": price, "organic_mismatch": False, **cmp}
            if best is None or cand["saving_cents"] > best["saving_cents"]:
                best = cand
    return best


def default_checkjebon(settings: Settings):
    from .stores import Checkjebon

    return Checkjebon(Path(settings.db_path).resolve().parent / "data" / "cache")
