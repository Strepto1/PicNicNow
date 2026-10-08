"""Kleine SQLite-laag. Eén bestand, geen ORM: alles is goed leesbaar met `sqlite3`."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,              -- Picnic product-id
    name TEXT NOT NULL,
    unit_quantity TEXT,
    price_cents INTEGER,
    image_id TEXT,
    is_organic INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    delivered_at TEXT,
    total_cents INTEGER,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS purchases (
    product_id TEXT NOT NULL REFERENCES products(id),
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    delivered_at TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    price_cents INTEGER,
    PRIMARY KEY (product_id, delivery_id)
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'zelf',     -- zelf | kant-en-klaar
    difficulty INTEGER NOT NULL DEFAULT 4, -- 4 = nieuw maar haalbaar, 5 = uitdagend
    familiarity TEXT NOT NULL DEFAULT 'nieuw', -- vaak | soms | nieuw
    ingredients TEXT NOT NULL DEFAULT '[]',    -- JSON [{name, qty, unit, category, shelf}]
    tags TEXT NOT NULL DEFAULT '[]',
    prep_minutes INTEGER,
    baby TEXT,                       -- JSON {stage-overstijgende tips, take_out_before, avoid}
    picnic_product_id TEXT,          -- voor kant-en-klare gerechten
    times_cooked INTEGER NOT NULL DEFAULT 0,
    last_cooked TEXT,
    rating INTEGER,
    source TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS plan (
    week TEXT NOT NULL,              -- ISO week, bv. 2026-W41
    day TEXT NOT NULL,               -- ma di wo do vr za zo
    slot TEXT NOT NULL DEFAULT 'diner', -- diner | lunch | baby-lunch | baby-ontbijt | baby-diner
    meal_id INTEGER REFERENCES meals(id),
    title TEXT,                      -- vrije tekst als er (nog) geen gerecht is
    servings INTEGER,
    note TEXT,
    cooked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (week, day, slot)
);

CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 1,
    unit TEXT,
    category TEXT,
    mode TEXT NOT NULL DEFAULT 'auto',   -- auto | kiezen | zelf
    product_id TEXT,
    product_name TEXT,
    product_count INTEGER,
    price_cents INTEGER,
    is_organic INTEGER,
    status TEXT NOT NULL DEFAULT 'open', -- open | in_mandje | gekocht | geschrapt
    source TEXT,                          -- gerecht / basis / gesprek / baby
    added_by TEXT,
    note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,          -- user | assistant
    speaker TEXT,
    content TEXT NOT NULL,       -- JSON: API content blocks (exact, voor replay)
    display TEXT,                -- leesbare tekst voor de UI
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    title TEXT NOT NULL,
    price_cents INTEGER,
    original_price_cents INTEGER,
    label TEXT,
    unit TEXT,
    valid_from TEXT,
    valid_until TEXT,
    url TEXT,
    is_organic INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS baby_foods (
    name TEXT PRIMARY KEY,
    is_allergen INTEGER NOT NULL DEFAULT 0,
    first_tried TEXT,
    last_given TEXT,
    times_given INTEGER NOT NULL DEFAULT 0,
    reaction TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchases_product ON purchases(product_id);
CREATE INDEX IF NOT EXISTS idx_list_week ON list_items(week);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        # opruimen: opvulprijzen (€4321,99) die eerder uit Picnic-zoekresultaten zijn opgeslagen
        for table in ("products", "purchases", "list_items"):
            self._conn.execute(f"UPDATE {table} SET price_cents = NULL WHERE price_cents = 432199")
        self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.tx() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def executemany(self, sql: str, rows: list[tuple] | list[dict]) -> None:
        with self.tx() as conn:
            conn.executemany(sql, rows)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- voorkeuren -------------------------------------------------------
    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM preferences WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_pref(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO preferences(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
