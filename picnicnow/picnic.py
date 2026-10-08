"""Koppeling met Picnic (onofficiële API via python-picnic-api2) + een demo-variant.

De rest van de app praat alleen met :class:`PicnicService`, zodat de demo-modus
(zonder account) en de echte Picnic-koppeling uitwisselbaar zijn.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Protocol

from .text import is_organic

log = logging.getLogger(__name__)

IMAGE_URL = "https://storefront-prod.nl.picnicinternational.com/static/images/{}/small.png"

# Picnic vult ``display_price`` op sommige zoektegels met een opvulwaarde (432199 = €4321,99);
# de echte prijs staat dan in ``price_ranges`` of in een PRICE-node van de tegel.
PLACEHOLDER_PRICES = {432199}
MAX_PLAUSIBLE_CENTS = 50_000  # €500: duurder verkoopt Picnic niet per stuk


def plausible_price(value) -> int | None:
    """Prijs in centen, of ``None`` als het een opvul- of onzinwaarde is."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    cents = round(value)
    if cents <= 0 or cents > MAX_PLAUSIBLE_CENTS or cents in PLACEHOLDER_PRICES:
        return None
    return cents


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def tile_price(item) -> int | None:
    """Beste prijs (centen) voor een zoektegel: display_price, dan price_ranges, dan PRICE-nodes."""
    price = plausible_price(item.display_price)
    if price is not None:
        return price
    ranges = [r for r in (item.price_ranges or []) if isinstance(r, dict)]
    ranges.sort(key=lambda r: r.get("from_quantity") or 0)
    for r in ranges:
        price = plausible_price(r.get("price"))
        if price is not None:
            return price
    for node in _walk(getattr(item, "raw", None)):
        if node.get("type") == "PRICE" and not node.get("isCrossed"):
            price = plausible_price(node.get("price"))
            if price is not None:
                return price
    return None


@dataclass
class ProductInfo:
    id: str
    name: str
    price_cents: int | None = None
    unit_quantity: str | None = None
    image_id: str | None = None
    is_organic: bool = False
    on_promo: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["image_url"] = IMAGE_URL.format(self.image_id) if self.image_id else None
        return d


@dataclass
class OrderedLine:
    product: ProductInfo
    quantity: int
    price_cents: int | None  # prijs per stuk


@dataclass
class PastDelivery:
    id: str
    delivered_at: str
    total_cents: int | None
    lines: list[OrderedLine] = field(default_factory=list)


@dataclass
class CartLine:
    product_id: str
    name: str
    quantity: int
    price_cents: int | None


@dataclass
class CartState:
    lines: list[CartLine]
    total_cents: int | None

    def to_dict(self) -> dict:
        return {"lines": [asdict(l) for l in self.lines], "total_cents": self.total_cents}


class TwoFactorRequired(Exception):
    """Picnic vraagt om een sms/e-mailcode."""


class PicnicUnavailable(Exception):
    """Picnic is niet bereikbaar of niet ingelogd."""


class PicnicService(Protocol):
    demo: bool

    def is_ready(self) -> bool: ...
    def search(self, term: str, limit: int = 8) -> list[ProductInfo]: ...
    def get_cart(self) -> CartState: ...
    def add_to_cart(self, product_id: str, count: int = 1) -> CartState: ...
    def remove_from_cart(self, product_id: str, count: int = 1) -> CartState: ...
    def order_history(self, max_deliveries: int = 30) -> list[PastDelivery]: ...


# ---------------------------------------------------------------------------
# Echte Picnic
# ---------------------------------------------------------------------------
class RealPicnic:
    demo = False

    def __init__(self, username: str, password: str, country: str = "NL", token: str | None = None,
                 on_token=None):
        from python_picnic_api2 import PicnicAPI

        self._username = username
        self._password = password
        self._on_token = on_token  # callback om het auth-token te bewaren
        self.api = PicnicAPI(country_code=country, auth_token=token)
        self.needs_2fa = False
        self._price_cache: dict[str, int | None] = {}

    # --- inloggen ----------------------------------------------------------
    def is_ready(self) -> bool:
        return self.api.logged_in() and not self.needs_2fa

    def login(self) -> None:
        from python_picnic_api2 import Picnic2FARequired

        try:
            self.api.login(self._username, self._password)
            self.needs_2fa = False
        except Picnic2FARequired as exc:
            self.needs_2fa = True
            raise TwoFactorRequired(str(exc)) from exc
        finally:
            self._store_token()

    def request_2fa(self, channel: str = "SMS") -> None:
        self.api.generate_2fa_code(channel)

    def verify_2fa(self, code: str) -> None:
        self.api.verify_2fa_code(code)
        self.needs_2fa = False
        self._store_token()

    def _store_token(self) -> None:
        token = self.api.session.auth_token
        if token and self._on_token:
            self._on_token(token)

    def _ensure(self) -> None:
        if not self.api.logged_in():
            self.login()
        if self.needs_2fa:
            raise PicnicUnavailable("Picnic wacht op de 2FA-code.")

    # --- producten ---------------------------------------------------------
    def search(self, term: str, limit: int = 8) -> list[ProductInfo]:
        self._ensure()
        result = self.api.search(term)
        out = []
        for item in result.items[:limit]:
            price = tile_price(item)
            if price is None:
                if item.display_price in PLACEHOLDER_PRICES:
                    log.info("Opvulprijs in zoektegel %s (%s), haal productpagina op", item.id, item.name)
                price = self._article_price(item.sole_article_id or item.id)
            promo = any((d or {}).get("type") in ("PROMO", "PRICE") for d in item.decorators if isinstance(d, dict))
            out.append(ProductInfo(
                id=item.sole_article_id or item.id,
                name=item.name or "",
                price_cents=price,
                unit_quantity=item.unit_quantity,
                image_id=item.image_id,
                is_organic=is_organic(item.name or ""),
                on_promo=promo,
            ))
        return out

    def _article_price(self, article_id: str) -> int | None:
        """Prijs van de productpagina, als de zoektegel geen bruikbare prijs had."""
        key = f"{date.today().isoformat()}:{article_id}"  # één keer per dag per product
        if key not in self._price_cache:
            try:
                art = self.api.get_article(article_id)
            except Exception as exc:  # een ontbrekende prijs mag het zoeken niet breken
                log.warning("Prijs voor %s niet opgehaald: %s", article_id, exc)
                return None
            self._price_cache[key] = plausible_price(art.price) if art else None
        return self._price_cache[key]

    # --- mandje ------------------------------------------------------------
    @staticmethod
    def _cart_state(cart) -> CartState:
        lines: list[CartLine] = []
        for line in cart.items:
            for art in line.items:
                qty = next((d.quantity for d in art.decorators if d.type == "QUANTITY" and d.quantity), 1)
                lines.append(CartLine(product_id=art.id or "", name=art.name or "", quantity=qty,
                                      price_cents=plausible_price(art.price)))
        return CartState(lines=lines, total_cents=cart.total_price)

    def get_cart(self) -> CartState:
        self._ensure()
        return self._cart_state(self.api.get_cart())

    def add_to_cart(self, product_id: str, count: int = 1) -> CartState:
        self._ensure()
        return self._cart_state(self.api.add_product(product_id, count))

    def remove_from_cart(self, product_id: str, count: int = 1) -> CartState:
        self._ensure()
        return self._cart_state(self.api.remove_product(product_id, count))

    # --- bestelgeschiedenis --------------------------------------------------
    def order_history(self, max_deliveries: int = 30) -> list[PastDelivery]:
        self._ensure()
        summaries = self.api.get_deliveries()
        done = [s for s in summaries if (s.status or "").upper() == "COMPLETED"][:max_deliveries]
        out: list[PastDelivery] = []
        for s in done:
            try:
                d = self.api.get_delivery(s.delivery_id)
            except Exception as exc:  # één kapotte levering mag de sync niet breken
                log.warning("Levering %s overgeslagen: %s", s.delivery_id, exc)
                continue
            when = (d.slot.window_start if d.slot else None) or d.creation_time or ""
            delivery = PastDelivery(id=d.delivery_id or s.delivery_id, delivered_at=when[:10],
                                    total_cents=sum((o.total_price or 0) for o in d.orders) or None)
            for order in d.orders:
                for line in order.items:
                    for art in line.items:
                        if not art.id:
                            continue
                        qty = next((dec.quantity for dec in art.decorators
                                    if dec.type == "QUANTITY" and dec.quantity), 1)
                        price = plausible_price(art.price)
                        delivery.lines.append(OrderedLine(
                            product=ProductInfo(id=art.id, name=art.name or "", price_cents=price,
                                                unit_quantity=art.unit_quantity,
                                                image_id=(art.image_ids or [None])[0],
                                                is_organic=is_organic(art.name or "")),
                            quantity=qty, price_cents=price))
            out.append(delivery)
        return out


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def load_demo_catalog() -> list[dict]:
    raw = resources.files("picnicnow.data").joinpath("demo_catalog.json").read_text(encoding="utf-8")
    return json.loads(raw)


class DemoPicnic:
    """Gedraagt zich als Picnic, met een vaste catalogus en verzonnen bestelhistorie."""

    demo = True

    def __init__(self, seed: int = 7):
        self.catalog = load_demo_catalog()
        self._by_id = {p["id"]: p for p in self.catalog}
        self._cart: dict[str, int] = {}
        self._seed = seed

    def is_ready(self) -> bool:
        return True

    def _info(self, p: dict) -> ProductInfo:
        return ProductInfo(id=p["id"], name=p["name"], price_cents=p["price_cents"],
                           unit_quantity=p["unit_quantity"], is_organic=is_organic(p["name"]))

    def search(self, term: str, limit: int = 8) -> list[ProductInfo]:
        from .text import similarity

        scored = [(similarity(term, p["name"]), p) for p in self.catalog]
        scored = [(s, p) for s, p in scored if s >= 0.35]
        scored.sort(key=lambda sp: -sp[0])
        return [self._info(p) for _, p in scored[:limit]]

    def get_cart(self) -> CartState:
        lines = [CartLine(product_id=pid, name=self._by_id[pid]["name"], quantity=q,
                          price_cents=self._by_id[pid]["price_cents"]) for pid, q in self._cart.items()]
        return CartState(lines=lines, total_cents=sum(l.quantity * (l.price_cents or 0) for l in lines))

    def add_to_cart(self, product_id: str, count: int = 1) -> CartState:
        if product_id not in self._by_id:
            raise PicnicUnavailable(f"Onbekend product {product_id}")
        self._cart[product_id] = self._cart.get(product_id, 0) + count
        return self.get_cart()

    def remove_from_cart(self, product_id: str, count: int = 1) -> CartState:
        left = self._cart.get(product_id, 0) - count
        if left > 0:
            self._cart[product_id] = left
        else:
            self._cart.pop(product_id, None)
        return self.get_cart()

    def order_history(self, max_deliveries: int = 30) -> list[PastDelivery]:
        rng = random.Random(self._seed)
        today = date.today()
        out = []
        n = min(max_deliveries, 10)
        for i in range(n):
            when = today - timedelta(days=7 * (n - i) - 2)
            d = PastDelivery(id=f"demo-{when.isoformat()}", delivered_at=when.isoformat(), total_cents=0)
            for p in self.catalog:
                w = p.get("history_weight", 0)
                if w and rng.random() < w / 10:
                    d.lines.append(OrderedLine(self._info(p), quantity=p.get("count", 1),
                                               price_cents=p["price_cents"]))
            d.total_cents = sum(l.quantity * (l.price_cents or 0) for l in d.lines)
            out.append(d)
        return list(reversed(out))


def make_picnic(settings, db) -> PicnicService:
    if settings.demo:
        return DemoPicnic()
    token = db.get_pref("picnic_token")
    return RealPicnic(settings.picnic_username, settings.picnic_password, settings.picnic_country,
                      token=token, on_token=lambda t: db.set_pref("picnic_token", t))


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
