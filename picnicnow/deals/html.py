"""Generieke lezer voor aanbiedingenpagina's (Vomar, DekaMarkt).

Supermarktsites veranderen regelmatig van opzet. In plaats van één fragiele selector
proberen we drie strategieën, van meest naar minst betrouwbaar:

1. JSON-LD (`<script type="application/ld+json">`) met Product/Offer
2. Ingebedde app-state (`__NEXT_DATA__`, `__NUXT__`, of andere JSON in <script>)
3. HTML-kaartjes: elementen met 'offer/promo/product/aanbieding' in de class en een prijs
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

from ..text import is_organic
from .base import Deal, to_cents

TITLE_KEYS = ("title", "name", "productName", "description", "headline", "displayName")
PRICE_KEYS = ("price", "offerPrice", "promotionPrice", "discountPrice", "salePrice", "newPrice", "priceNow",
              "currentPrice", "actionPrice", "promoPrice")
OLD_PRICE_KEYS = ("originalPrice", "oldPrice", "regularPrice", "priceBefore", "priceWas", "fromPrice",
                  "normalPrice", "priceBeforeBonus")
LABEL_KEYS = ("label", "priceLabel", "discountLabel", "promotionText", "discountType", "mechanism",
              "offerText", "badge", "sticker")
UNIT_KEYS = ("unit", "descriptiveSize", "size", "packaging", "contentDescription", "unitSize", "weight")
FROM_KEYS = ("validFrom", "startDate", "start", "dateFrom", "availabilityStarts")
UNTIL_KEYS = ("validUntil", "endDate", "end", "dateTo", "priceValidUntil", "availabilityEnds")

_PRICE_TEXT = re.compile(r"(?:€\s*)?(\d{1,3})[.,](\d{2})")


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _walk(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _price(value: Any) -> int | None:
    if isinstance(value, dict):  # {"amount": 2.49} / {"value": "2,49"}
        value = _first(value, ("amount", "value", "price", "now"))
    return to_cents(value) if value is not None else None


def deals_from_json(data: Any, store: str, base_url: str = "") -> list[Deal]:
    out: dict[str, Deal] = {}
    for node in _walk(data):
        title = _first(node, TITLE_KEYS)
        if not isinstance(title, str) or not (3 <= len(title) <= 120):
            continue
        price = _price(_first(node, PRICE_KEYS))
        old = _price(_first(node, OLD_PRICE_KEYS))
        offers = node.get("offers")
        if price is None and isinstance(offers, (dict, list)):  # JSON-LD Product
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offer, dict):
                price = _price(_first(offer, ("price", "lowPrice")))
                node = {**node, **{k: v for k, v in offer.items() if k in FROM_KEYS + UNTIL_KEYS}}
        label = _first(node, LABEL_KEYS)
        if price is None and not label:
            continue
        if price is not None and not (5 <= price <= 20000):
            continue
        unit = _first(node, UNIT_KEYS)
        url = node.get("url") or node.get("link") or node.get("slug")
        if isinstance(url, str) and url.startswith("/"):
            url = base_url.rstrip("/") + url
        deal = Deal(store=store, title=title.strip(), price_cents=price, original_price_cents=old,
                    label=str(label) if label and not isinstance(label, (dict, list)) else None,
                    unit=str(unit) if unit and not isinstance(unit, (dict, list)) else None,
                    valid_from=str(_first(node, FROM_KEYS) or "") or None,
                    valid_until=str(_first(node, UNTIL_KEYS) or "") or None,
                    url=url if isinstance(url, str) else None,
                    is_organic=is_organic(title, str(node.get("brand") or "")))
        key = f"{deal.title}|{deal.price_cents}"
        out.setdefault(key, deal)
    return list(out.values())


def _script_json(soup: BeautifulSoup) -> Iterator[Any]:
    for tag in soup.find_all("script"):
        text = tag.string or tag.get_text() or ""
        if tag.get("type") in ("application/ld+json", "application/json") or tag.get("id") == "__NEXT_DATA__":
            try:
                yield json.loads(text)
            except ValueError:
                continue
        elif "__NUXT__" in text or "window.__" in text:
            m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S)
            if m:
                try:
                    yield json.loads(m.group(1))
                except ValueError:
                    continue


def deals_from_cards(soup: BeautifulSoup, store: str, base_url: str = "") -> list[Deal]:
    pattern = re.compile(r"offer|promo|product|aanbieding|deal|tile|card", re.I)
    out: dict[str, Deal] = {}
    for el in soup.find_all(class_=pattern):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not (8 <= len(text) <= 300):
            continue
        prices = [int(a) * 100 + int(b) for a, b in _PRICE_TEXT.findall(text)]
        heading = el.find(["h2", "h3", "h4", "strong"]) or el.find(class_=re.compile(r"title|name", re.I))
        title = heading.get_text(" ", strip=True) if heading else None
        if not title or not prices or len(title) > 120:
            continue
        label_el = el.find(class_=re.compile(r"label|badge|sticker|discount|korting|actie", re.I))
        label = label_el.get_text(" ", strip=True) if label_el else None
        link = el.find("a", href=True)
        url = link["href"] if link else None
        if url and url.startswith("/"):
            url = base_url.rstrip("/") + url
        price = min(prices)
        old = max(prices) if len(prices) > 1 and max(prices) > price else None
        key = f"{title}|{price}"
        if key not in out:
            out[key] = Deal(store=store, title=title, price_cents=price, original_price_cents=old,
                            label=label, url=url, is_organic=is_organic(title))
    return list(out.values())


def parse_offers_page(html: str, store: str, base_url: str = "") -> list[Deal]:
    soup = BeautifulSoup(html, "html.parser")
    deals: list[Deal] = []
    for data in _script_json(soup):
        deals += deals_from_json(data, store, base_url)
    if not deals:
        deals = deals_from_cards(soup, store, base_url)
    # dubbele titels samenvoegen (laagste prijs wint)
    best: dict[str, Deal] = {}
    for d in deals:
        cur = best.get(d.title.lower())
        if cur is None or (d.price_cents or 10**9) < (cur.price_cents or 10**9):
            best[d.title.lower()] = d
    return list(best.values())
