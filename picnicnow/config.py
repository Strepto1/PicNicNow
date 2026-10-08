"""Instellingen, gelezen uit omgevingsvariabelen (of een .env-bestand)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:  # .env is optioneel
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass
class Settings:
    picnic_username: str = ""
    picnic_password: str = ""
    picnic_country: str = "NL"

    anthropic_api_key: str = ""
    model: str = "claude-opus-5-5"
    effort: str = "medium"

    names: list[str] = field(default_factory=lambda: ["Ik", "Partner"])
    baby_name: str = "Baby"
    baby_birthdate: date | None = None
    persons: int = 2
    organic_level: int = 2
    organic_max_premium: int = 40

    deal_stores: list[str] = field(default_factory=lambda: ["ah", "vomar", "dekamarkt"])
    deal_min_price_cents: int = 250
    dekamarkt_api_id: str = ""
    dekamarkt_api_key: str = ""
    dekamarkt_store_id: str = ""

    db_path: Path = Path("picnicnow.db")
    host: str = "0.0.0.0"
    port: int = 8000
    pin: str = ""

    @property
    def demo(self) -> bool:
        """Demo-modus: geen Picnic-inloggegevens, dus voorbeelddata gebruiken."""
        return not (self.picnic_username and self.picnic_password)

    @property
    def assistant_enabled(self) -> bool:
        return bool(self.anthropic_api_key or _env("ANTHROPIC_AUTH_TOKEN"))

    @classmethod
    def from_env(cls) -> "Settings":
        names = [n.strip() for n in _env("PICNICNOW_NAMES", "Ik,Partner").split(",") if n.strip()]
        stores = [s.strip().lower() for s in _env("PICNICNOW_DEAL_STORES", "ah,vomar,dekamarkt").split(",") if s.strip()]
        try:
            min_price = round(float(_env("PICNICNOW_DEAL_MIN_PRICE", "2.50").replace(",", ".")) * 100)
        except ValueError:
            min_price = 250
        return cls(
            picnic_username=_env("PICNIC_USERNAME"),
            picnic_password=_env("PICNIC_PASSWORD"),
            picnic_country=_env("PICNIC_COUNTRY", "NL") or "NL",
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            model=_env("PICNICNOW_MODEL", "claude-opus-5-5") or "claude-opus-5-5",
            effort=_env("PICNICNOW_EFFORT", "medium") or "medium",
            names=names or ["Ik", "Partner"],
            baby_name=_env("PICNICNOW_BABY_NAME", "Baby") or "Baby",
            baby_birthdate=_parse_date(_env("PICNICNOW_BABY_BIRTHDATE")),
            persons=int(_env("PICNICNOW_PERSONS", "2") or 2),
            organic_level=int(_env("PICNICNOW_ORGANIC", "2") or 2),
            organic_max_premium=int(_env("PICNICNOW_ORGANIC_MAX_PREMIUM", "40") or 40),
            deal_stores=stores,
            deal_min_price_cents=min_price,
            dekamarkt_api_id=_env("DEKAMARKT_API_ID"),
            dekamarkt_api_key=_env("DEKAMARKT_API_KEY"),
            dekamarkt_store_id=_env("DEKAMARKT_STORE_ID"),
            db_path=Path(_env("PICNICNOW_DB", "picnicnow.db") or "picnicnow.db"),
            host=_env("PICNICNOW_HOST", "0.0.0.0") or "0.0.0.0",
            port=int(_env("PICNICNOW_PORT", "8000") or 8000),
            pin=_env("PICNICNOW_PIN"),
        )
