"""Gemeenschappelijke types voor aanbiedingen-bronnen."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx

USER_AGENT = "Mozilla/5.0 (PicNicNow boodschappenhulp; persoonlijk gebruik)"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)


@dataclass
class Deal:
    store: str
    title: str
    price_cents: int | None = None
    original_price_cents: int | None = None
    label: str | None = None          # bv. '2e halve prijs', '25% korting'
    unit: str | None = None           # verpakkingsgrootte
    valid_from: str | None = None
    valid_until: str | None = None
    url: str | None = None
    is_organic: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class DealProvider(Protocol):
    store: str

    def fetch(self, watch_terms: list[str]) -> list[Deal]: ...


_PRICE_RE = re.compile(r"(\d{1,3})[.,](\d{2})")


def to_cents(value) -> int | None:
    """Euro's naar centen: '2,49' / 2.49 / '€ 2.49' / 3 -> 249 / 249 / 249 / 300."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(value * 100)
    m = _PRICE_RE.search(str(value))
    if m:
        return int(m.group(1)) * 100 + int(m.group(2))
    digits = re.sub(r"\D", "", str(value))
    return int(digits) * 100 if digits and len(digits) <= 3 else None


def effective_price(deal: "Deal") -> int | None:
    """Wat je per stuk betaalt. Is de actieprijs al verrekend (prijs < van-prijs), dan die;
    anders de actie toepassen ('1+1 gratis', '2e halve prijs', '25% korting')."""
    if deal.price_cents and deal.original_price_cents and deal.price_cents < deal.original_price_cents:
        return deal.price_cents
    return _apply_label(deal.price_cents or deal.original_price_cents, deal.label)


def _apply_label(price_cents: int | None, label: str | None) -> int | None:
    if price_cents is None or not label:
        return price_cents
    l = label.lower()
    m = re.search(r"(\d+)\s*\+\s*(\d+)\s*gratis", l)
    if m:
        buy, free = int(m.group(1)), int(m.group(2))
        return round(price_cents * buy / (buy + free))
    if "2e halve prijs" in l or "tweede halve prijs" in l:
        return round(price_cents * 0.75)
    m = re.search(r"(\d+)\s*%\s*korting", l)
    if m and "voor" not in l:
        return round(price_cents * (100 - int(m.group(1))) / 100)
    return price_cents
