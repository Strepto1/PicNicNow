"""Bestelgeschiedenis: synchroniseren vanuit Picnic en analyseren (vaste boodschappen)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .db import Database, now_iso
from .picnic import PicnicService
from .text import categorize, similarity


def sync_history(db: Database, picnic: PicnicService, max_deliveries: int = 30) -> dict:
    deliveries = picnic.order_history(max_deliveries)
    new = 0
    with db.tx() as conn:
        for d in deliveries:
            exists = conn.execute("SELECT 1 FROM deliveries WHERE id = ?", (d.id,)).fetchone()
            conn.execute(
                "INSERT INTO deliveries(id, delivered_at, total_cents, synced_at) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET delivered_at=excluded.delivered_at, total_cents=excluded.total_cents",
                (d.id, d.delivered_at, d.total_cents, now_iso()),
            )
            new += 0 if exists else 1
            for line in d.lines:
                p = line.product
                conn.execute(
                    "INSERT INTO products(id, name, unit_quantity, price_cents, image_id, is_organic, category, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                    "unit_quantity=COALESCE(excluded.unit_quantity, products.unit_quantity), "
                    "price_cents=COALESCE(excluded.price_cents, products.price_cents), "
                    "image_id=COALESCE(excluded.image_id, products.image_id), "
                    "is_organic=excluded.is_organic, updated_at=excluded.updated_at",
                    (p.id, p.name, p.unit_quantity, p.price_cents, p.image_id, int(p.is_organic),
                     categorize(p.name), now_iso()),
                )
                conn.execute(
                    "INSERT INTO purchases(product_id, delivery_id, delivered_at, quantity, price_cents) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(product_id, delivery_id) DO UPDATE SET "
                    "quantity=excluded.quantity, price_cents=excluded.price_cents",
                    (p.id, d.id, d.delivered_at, line.quantity, line.price_cents),
                )
    db.set_pref("last_sync", now_iso())
    return {"deliveries": len(deliveries), "new": new,
            "products": db.one("SELECT COUNT(*) AS n FROM products")["n"]}


@dataclass
class Staple:
    product_id: str
    name: str
    unit_quantity: str | None
    price_cents: int | None
    is_organic: bool
    category: str | None
    times: int              # in hoeveel leveringen
    share: float            # aandeel van alle leveringen
    avg_quantity: float
    avg_interval_days: float | None
    last_bought: str | None
    days_since: int | None

    @property
    def due(self) -> bool:
        """Is het weer tijd? (≥ 85% van het gebruikelijke interval verstreken)"""
        if self.days_since is None or not self.avg_interval_days:
            return False
        return self.days_since >= 0.85 * self.avg_interval_days

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["due"] = self.due
        return d


def staples(db: Database, min_times: int = 2, today: date | None = None) -> list[Staple]:
    today = today or date.today()
    total = db.one("SELECT COUNT(*) AS n FROM deliveries")["n"] or 0
    if not total:
        return []
    rows = db.query(
        "SELECT p.id, p.name, p.unit_quantity, p.price_cents, p.is_organic, p.category, "
        "COUNT(*) AS times, AVG(pu.quantity) AS avg_q, GROUP_CONCAT(pu.delivered_at) AS dates "
        "FROM purchases pu JOIN products p ON p.id = pu.product_id "
        "GROUP BY p.id HAVING COUNT(*) >= ? ORDER BY times DESC, p.name",
        (min_times,),
    )
    out = []
    for r in rows:
        dates = sorted({x for x in (r["dates"] or "").split(",") if x})
        parsed = [date.fromisoformat(x[:10]) for x in dates]
        interval = None
        if len(parsed) >= 2:
            gaps = [(b - a).days for a, b in zip(parsed, parsed[1:])]
            interval = sum(gaps) / len(gaps)
        last = parsed[-1] if parsed else None
        out.append(Staple(
            product_id=r["id"], name=r["name"], unit_quantity=r["unit_quantity"],
            price_cents=r["price_cents"], is_organic=bool(r["is_organic"]), category=r["category"],
            times=r["times"], share=round(r["times"] / total, 2), avg_quantity=round(r["avg_q"] or 1, 1),
            avg_interval_days=round(interval, 1) if interval else None,
            last_bought=last.isoformat() if last else None,
            days_since=(today - last).days if last else None,
        ))
    return out


def due_basics(db: Database, min_share: float = 0.4, today: date | None = None) -> list[Staple]:
    """Vaste boodschappen die volgens het ritme weer op de lijst horen."""
    return [s for s in staples(db, today=today) if s.share >= min_share and s.due]


def history_matches(db: Database, query: str, limit: int = 5, min_score: float = 0.45) -> list[dict]:
    """Eerder gekochte producten die op `query` lijken, met hoe vaak ze gekocht zijn."""
    rows = db.query(
        "SELECT p.*, COUNT(pu.delivery_id) AS times, MAX(pu.delivered_at) AS last_bought "
        "FROM products p LEFT JOIN purchases pu ON pu.product_id = p.id GROUP BY p.id"
    )
    scored = []
    for r in rows:
        s = similarity(query, r["name"])
        if s >= min_score:
            r["score"] = s
            scored.append(r)
    scored.sort(key=lambda r: (-r["score"], -(r["times"] or 0)))
    return scored[:limit]
