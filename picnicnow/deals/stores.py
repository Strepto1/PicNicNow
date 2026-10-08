"""Vomar en DekaMarkt (aanbiedingen) + Checkjebon (reguliere prijzen, open dataset)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from ..text import is_organic
from .base import TIMEOUT, USER_AGENT, Deal, to_cents
from .html import parse_offers_page

log = logging.getLogger(__name__)


class WebsiteOffers:
    """Leest de publieke aanbiedingenpagina van een supermarkt."""

    def __init__(self, store: str, urls: list[str], base_url: str, client: httpx.Client | None = None):
        self.store = store
        self.urls = urls
        self.base_url = base_url
        self._client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                                              headers={"User-Agent": USER_AGENT, "Accept-Language": "nl-NL"})

    def fetch(self, watch_terms: list[str]) -> list[Deal]:
        deals: list[Deal] = []
        for url in self.urls:
            try:
                r = self._client.get(url)
                r.raise_for_status()
                deals += parse_offers_page(r.text, self.store, self.base_url)
            except httpx.HTTPError as exc:
                log.warning("%s: %s niet bereikbaar: %s", self.store, url, exc)
        return deals


def Vomar(client: httpx.Client | None = None) -> WebsiteOffers:
    return WebsiteOffers("vomar", ["https://www.vomar.nl/aanbiedingen"], "https://www.vomar.nl", client)


class DekaMarktApp:
    """DekaMarkt via de app-API (gedeeld met Dirk). Vereist eigen x-api-id/x-api-key (zie README)."""

    store = "dekamarkt"
    BASE = "https://app-api.dirk.nl/v2"

    def __init__(self, api_id: str, api_key: str, store_id: str, client: httpx.Client | None = None):
        self.store_id = store_id
        self._client = client or httpx.Client(timeout=TIMEOUT, headers={
            "x-api-id": api_id, "x-api-key": api_key, "User-Agent": "okhttp/4.9.1"})

    def fetch(self, watch_terms: list[str]) -> list[Deal]:
        try:
            r = self._client.get(f"{self.BASE}/catalog/offers", params={"storeId": self.store_id})
            r.raise_for_status()
            return parse_dirk_offers(r.json())
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("DekaMarkt-app-API mislukt: %s", exc)
            return []


def parse_dirk_offers(data) -> list[Deal]:
    offers = data if isinstance(data, list) else data.get("offers", data.get("items", []))
    out = []
    for o in offers:
        title = o.get("title") or ""
        names = " ".join(p.get("name", "") + " " + (p.get("brandName") or "") for p in o.get("products") or [])
        out.append(Deal(
            store="dekamarkt", title=title,
            price_cents=to_cents(o.get("discountPrice")), original_price_cents=to_cents(o.get("originalPrice")),
            label=o.get("priceLabel") or o.get("discountType"), unit=o.get("descriptiveSize"),
            valid_from=o.get("validFrom"), valid_until=o.get("validUntil"),
            is_organic=is_organic(title, names),
        ))
    return out


def DekaMarkt(settings, client: httpx.Client | None = None):
    if settings.dekamarkt_api_id and settings.dekamarkt_api_key and settings.dekamarkt_store_id:
        return DekaMarktApp(settings.dekamarkt_api_id, settings.dekamarkt_api_key, settings.dekamarkt_store_id, client)
    return WebsiteOffers("dekamarkt", ["https://www.dekamarkt.nl/aanbiedingen"], "https://www.dekamarkt.nl", client)


# ---------------------------------------------------------------------------
# Checkjebon: dagelijks bijgewerkte reguliere prijzen van o.a. AH, DekaMarkt, Vomar
# (https://github.com/supermarkt/checkjebon). Handig om te zien waar iets
# structureel goedkoper is – ook zonder actie.
# ---------------------------------------------------------------------------
CHECKJEBON_URL = "https://raw.githubusercontent.com/supermarkt/checkjebon/main/data/supermarkets.json"


class Checkjebon:
    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, max_age_hours: int = 24):
        self.cache = cache_dir / "checkjebon.json"
        self.max_age = max_age_hours * 3600
        self._client = client
        self._data: dict[str, list[dict]] | None = None

    def _load(self) -> dict[str, list[dict]]:
        if self._data is not None:
            return self._data
        fresh = self.cache.exists() and time.time() - self.cache.stat().st_mtime < self.max_age
        if not fresh:
            try:
                client = self._client or httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=True)
                r = client.get(CHECKJEBON_URL)
                r.raise_for_status()
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_bytes(r.content)
            except httpx.HTTPError as exc:
                log.warning("Checkjebon-data niet op te halen: %s", exc)
        if not self.cache.exists():
            self._data = {}
            return self._data
        raw = json.loads(self.cache.read_text(encoding="utf-8"))
        self._data = {s["n"]: s.get("d") or [] for s in raw}
        self._urls = {s["n"]: s.get("u") for s in raw}
        return self._data

    def prices(self, store: str) -> list[dict]:
        """[{n: naam, p: prijs (euro), s: grootte, l: link}]"""
        return self._load().get(store, [])

    def url(self, store: str, link: str | None) -> str | None:
        self._load()
        base = getattr(self, "_urls", {}).get(store)
        return f"{base}{link}" if base and link else None
