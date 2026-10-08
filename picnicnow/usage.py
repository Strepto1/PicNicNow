"""Wat kost de assistent? Houdt tokengebruik per maand bij en bewaakt het maandbudget."""

from __future__ import annotations

from datetime import date

from .db import Database, now_iso

# $ per miljoen tokens: (invoer, uitvoer, cache lezen, cache schrijven). Stand oktober 2026.
# Cache schrijven = 1,25× invoer. Controleer actuele prijzen op https://claude.com/pricing.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-fable-5-1": (10.00, 50.00, 0.25, 12.50),
}

MODEL_CHOICES = [
    {"id": "claude-opus-5-5", "label": "Opus 5.5 – slimst, ± $4–8 per maand"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5 – prima, ± $2–4 per maand"},
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5 – bijna gratis, ± $0,10–0,50 per maand"},
]

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER, output_tokens INTEGER,
    cost_usd REAL,
    created_at TEXT
);
"""


def _ensure(db: Database) -> None:
    with db.tx() as conn:
        conn.executescript(USAGE_SCHEMA)


def cost_of(model: str, usage) -> float:
    def g(name: str) -> int:
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return int(v or 0)

    price = PRICES.get(model) or next((v for k, v in PRICES.items() if model.startswith(k)), PRICES["claude-opus-5-5"])
    inp, out, read, write = price
    return (g("input_tokens") * inp + g("output_tokens") * out + g("cache_read_input_tokens") * read
            + g("cache_creation_input_tokens") * write) / 1_000_000


def record(db: Database, model: str, usage) -> float:
    _ensure(db)
    cost = cost_of(model, usage)

    def g(name: str) -> int:
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return int(v or 0)

    db.execute(
        "INSERT INTO api_usage(month, model, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, "
        "cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (date.today().strftime("%Y-%m"), model, g("input_tokens"), g("cache_creation_input_tokens"),
         g("cache_read_input_tokens"), g("output_tokens"), cost, now_iso()),
    )
    return cost


def month_summary(db: Database, month: str | None = None) -> dict:
    _ensure(db)
    month = month or date.today().strftime("%Y-%m")
    row = db.one(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(input_tokens + "
        "cache_write_tokens + cache_read_tokens), 0) AS tokens_in, COALESCE(SUM(output_tokens), 0) AS tokens_out "
        "FROM api_usage WHERE month = ?", (month,))
    return {"month": month, "calls": row["calls"], "cost_usd": round(row["cost"], 4),
            "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"]}


def over_budget(db: Database, budget_usd: float | None) -> bool:
    if not budget_usd or budget_usd <= 0:
        return False
    return month_summary(db)["cost_usd"] >= budget_usd
