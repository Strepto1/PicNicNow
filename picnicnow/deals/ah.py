"""Albert Heijn bonus via de (onofficiële) mobiele API die ook de Appie-app gebruikt.

Endpoints volgens het open-source project gwillem/appie-go:
  POST /mobile-auth/v1/auth/token/anonymous   {"clientId": "appie-ios"}
  GET  /mobile-services/product/search/v2?query=…&sortOn=RELEVANCE&page=0&size=…
"""

from __future__ import annotations

import logging
import time

import httpx

from ..text import is_organic
from .base import TIMEOUT, Deal, to_cents

log = logging.getLogger(__name__)

BASE = "https://api.ah.nl"
HEADERS = {
    "User-Agent": "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)",
    "x-client-name": "appie-ios",
    "x-client-version": "9.28",
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def parse_product(p: dict) -> Deal | None:
    """Zet een AH-product om in een Deal, alleen als het in de bonus is."""
    if not p.get("isBonus"):
        return None
    title = p.get("title") or ""
    icons = " ".join(p.get("propertyIcons") or [])
    current = to_cents(p.get("currentPrice"))
    before = to_cents(p.get("priceBeforeBonus"))
    return Deal(
        store="ah",
        title=title,
        price_cents=current or before,
        original_price_cents=before,
        label=p.get("bonusMechanism"),
        unit=p.get("salesUnitSize"),
        valid_from=p.get("bonusStartDate"),
        valid_until=p.get("bonusEndDate"),
        url=f"https://www.ah.nl/producten/product/wi{p['webshopId']}" if p.get("webshopId") else None,
        is_organic=is_organic(title, icons + " " + (p.get("brand") or "")),
    )


class AlbertHeijn:
    store = "ah"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS)
        self._token: str | None = None

    def _auth(self) -> None:
        r = self._client.post(f"{BASE}/mobile-auth/v1/auth/token/anonymous", json={"clientId": "appie-ios"})
        r.raise_for_status()
        self._token = r.json()["access_token"]
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    def search(self, term: str, size: int = 15) -> list[dict]:
        if not self._token:
            self._auth()
        r = self._client.get(f"{BASE}/mobile-services/product/search/v2",
                             params={"query": term, "sortOn": "RELEVANCE", "page": 0, "size": size})
        if r.status_code == 401:  # token verlopen
            self._auth()
            r = self._client.get(f"{BASE}/mobile-services/product/search/v2",
                                 params={"query": term, "sortOn": "RELEVANCE", "page": 0, "size": size})
        r.raise_for_status()
        return r.json().get("products", [])

    def fetch(self, watch_terms: list[str]) -> list[Deal]:
        deals: dict[str, Deal] = {}
        for term in watch_terms:
            try:
                for p in self.search(term):
                    d = parse_product(p)
                    if d:
                        deals[d.title] = d
            except httpx.HTTPError as exc:
                log.warning("AH zoeken naar %r mislukt: %s", term, exc)
            time.sleep(0.2)  # vriendelijk blijven
        return list(deals.values())
