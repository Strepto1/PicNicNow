"""Boodschappenlijst: toevoegen, samenvoegen, producten kiezen (historie + bio) en naar het Picnic-mandje."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .config import Settings
from .db import Database, now_iso
from .history import due_basics, history_matches
from .picnic import PicnicService
from .text import categorize, category_order, normalize, similarity, tokens

MODES = ("auto", "kiezen", "zelf")
DEFAULT_MODE_RULES = {"tomaat": "kiezen"}

_UNIT_RE = re.compile(r"(?:(\d+)\s*x\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|gram|ml|cl|l|liter|stuks?|st)\b", re.I)


def parse_amount(text: str | None) -> tuple[float, str] | None:
    """'1,5 kg' -> (1500, 'g'); '6 stuks' -> (6, 'stuk'); '4x56 stuks' -> (224, 'stuk')."""
    if not text:
        return None
    m = _UNIT_RE.search(text)
    if not m:
        return None
    mult = int(m.group(1)) if m.group(1) else 1
    value = float(m.group(2).replace(",", ".")) * mult
    unit = m.group(3).lower()
    if unit == "kg":
        return value * 1000, "g"
    if unit in ("g", "gram"):
        return value, "g"
    if unit == "l" or unit == "liter":
        return value * 1000, "ml"
    if unit == "cl":
        return value * 10, "ml"
    if unit == "ml":
        return value, "ml"
    return value, "stuk"


def packs_needed(quantity: float | None, unit: str | None, product_unit: str | None) -> int:
    qty = quantity or 1
    u = (unit or "").lower().strip()
    if u in ("kg", "g", "gram", "l", "liter", "ml", "cl"):
        need = parse_amount(f"{qty} {u}")
        have = parse_amount(product_unit)
        if need and have and need[1] == have[1] and have[0] > 0:
            return max(1, math.ceil(need[0] / have[0] - 0.15))  # 15% marge: 520 g ≈ 1 pak van 500 g
        return 1
    if u in ("", "stuk", "stuks", "st"):
        have = parse_amount(product_unit)
        if have and have[1] == "stuk" and have[0] > 1:
            return max(1, math.ceil(qty / have[0]))
        return max(1, math.ceil(qty))
    if u in ("blik", "blikje", "pak", "potje", "pot", "zak", "zakje", "fles", "doos", "rol", "bos", "krop", "pakket"):
        return max(1, math.ceil(qty))
    return 1  # teen, el, tl, snee, snuf, bosje… -> 1 verpakking


@dataclass
class Candidate:
    id: str
    name: str
    price_cents: int | None
    unit_quantity: str | None
    is_organic: bool
    times_bought: int
    similarity: float
    source: str
    score: float = 0.0
    on_promo: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class ShoppingList:
    def __init__(self, db: Database, picnic: PicnicService, settings: Settings):
        self.db = db
        self.picnic = picnic
        self.settings = settings

    # --- voorkeuren ------------------------------------------------------------
    @property
    def organic_level(self) -> int:
        return int(self.db.get_pref("organic_level", self.settings.organic_level))

    def mode_rules(self) -> dict[str, str]:
        return self.db.get_pref("mode_rules", DEFAULT_MODE_RULES)

    def set_mode_rule(self, keyword: str, mode: str) -> dict[str, str]:
        if mode not in MODES:
            raise ValueError(f"mode moet een van {MODES} zijn")
        rules = self.mode_rules()
        if mode == "auto":
            rules.pop(normalize(keyword), None)
        else:
            rules[normalize(keyword)] = mode
        self.db.set_pref("mode_rules", rules)
        return rules

    def mode_for(self, name: str) -> str:
        toks = tokens(name)
        for keyword, mode in self.mode_rules().items():
            kt = tokens(keyword)
            if kt and all(any(k in t or t in k for t in toks) for k in kt):
                return mode
        return "auto"

    # --- lijst -------------------------------------------------------------------
    def items(self, week: str, include_removed: bool = False) -> list[dict]:
        rows = self.db.query("SELECT * FROM list_items WHERE week = ?" +
                             ("" if include_removed else " AND status != 'geschrapt'"), (week,))
        rows.sort(key=lambda r: (category_order(r["category"] or "Overig"), r["name"]))
        return rows

    def get(self, item_id: int) -> dict | None:
        return self.db.one("SELECT * FROM list_items WHERE id = ?", (item_id,))

    def _find_open(self, week: str, name: str) -> dict | None:
        n = normalize(name)
        for r in self.db.query("SELECT * FROM list_items WHERE week = ? AND status IN ('open','in_mandje')", (week,)):
            if normalize(r["name"]) == n or similarity(name, r["name"]) >= 0.9:
                return r
        return None

    def add(self, week: str, name: str, quantity: float | None = 1, unit: str | None = None, *,
            source: str = "gesprek", added_by: str | None = None, mode: str | None = None,
            note: str | None = None, organic: bool | None = None) -> dict:
        name = name.strip()
        quantity = quantity or 1
        existing = self._find_open(week, name)
        if existing:
            same_unit = (existing["unit"] or "") == (unit or "")
            new_q = existing["quantity"] + quantity if same_unit else existing["quantity"]
            sources = {s for s in (existing["source"] or "").split(", ") if s} | {source}
            notes = existing["note"] or ""
            if not same_unit:
                extra = f"+ {quantity:g} {unit or ''}".strip()
                notes = f"{notes}; {extra}".strip("; ")
            self.db.execute(
                "UPDATE list_items SET quantity = ?, source = ?, note = ?, mode = COALESCE(?, mode), "
                "product_count = NULL WHERE id = ?",
                (new_q, ", ".join(sorted(sources)), notes or None, mode, existing["id"]),
            )
            item = self.get(existing["id"])
            if item["product_id"]:
                self._set_count(item)
            return self.get(existing["id"])
        item_id = self.db.execute(
            "INSERT INTO list_items(week, name, quantity, unit, category, mode, status, source, added_by, note, "
            "is_organic, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (week, name, quantity, unit, categorize(name), mode or self.mode_for(name), "open", source, added_by,
             note, None if organic is None else int(organic), now_iso()),
        )
        return self.get(item_id)

    def add_meal(self, week: str, meal: dict, servings: int | None = None, added_by: str | None = None) -> list[dict]:
        """Ingrediënten van een gerecht op de lijst (voorraadkast-items alleen als notitie)."""
        base = meal.get("servings") or 2
        factor = (servings or self.settings.persons) / base
        added = []
        for ing in meal["ingredients"]:
            if ing.get("pantry"):
                continue
            qty = ing.get("qty", 1)
            if isinstance(qty, (int, float)):
                qty = qty * factor
                qty = round(qty) if qty >= 10 else round(qty, 1)
            if meal.get("kind") == "kant-en-klaar" and meal.get("picnic_product_id"):
                item = self.add(week, meal["name"], max(1, round(factor)), "stuk", source=meal["name"], added_by=added_by)
                self.choose(item["id"], meal["picnic_product_id"])
                added.append(self.get(item["id"]))
                break
            added.append(self.add(week, ing["name"], qty, ing.get("unit"), source=meal["name"], added_by=added_by))
        return added

    def pantry_check(self, meal: dict) -> list[str]:
        return [i["name"] for i in meal["ingredients"] if i.get("pantry")]

    def add_basics(self, week: str, added_by: str | None = None) -> list[dict]:
        out = []
        open_items = [i for i in self.items(week) if i["status"] in ("open", "in_mandje")]
        for s in due_basics(self.db):
            if any(i["product_id"] == s.product_id or similarity(i["name"], s.name) >= 0.7
                   or similarity(s.name, i["name"]) >= 0.7 for i in open_items):
                continue  # staat er al op (bv. via een gerecht)
            item = self.add(week, s.name, s.avg_quantity, "stuk", source="vaste boodschap", added_by=added_by)
            if not item["product_id"]:
                self._apply(item, Candidate(s.product_id, s.name, s.price_cents, s.unit_quantity, s.is_organic,
                                            s.times, 1.0, "historie"))
                self.db.execute("UPDATE list_items SET product_count = ? WHERE id = ?",
                                (max(1, round(s.avg_quantity)), item["id"]))
            out.append(self.get(item["id"]))
        return out

    def update(self, item_id: int, **fields) -> dict | None:
        allowed = {"name", "quantity", "unit", "mode", "status", "note", "product_count", "is_organic"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "mode" in sets and sets["mode"] not in MODES:
            raise ValueError(f"mode moet een van {MODES} zijn")
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            self.db.execute(f"UPDATE list_items SET {cols} WHERE id = ?", (*sets.values(), item_id))
        if "name" in sets:
            self.db.execute("UPDATE list_items SET product_id = NULL, product_name = NULL, price_cents = NULL, "
                            "category = ? WHERE id = ?", (categorize(sets["name"]), item_id))
        return self.get(item_id)

    def remove(self, item_id: int) -> None:
        self.db.execute("UPDATE list_items SET status = 'geschrapt' WHERE id = ?", (item_id,))

    def find(self, week: str, name: str) -> dict | None:
        rows = self.items(week)
        best = max(rows, key=lambda r: similarity(name, r["name"]), default=None)
        return best if best and similarity(name, best["name"]) >= 0.5 else None

    # --- productkeuze --------------------------------------------------------------
    def candidates(self, name: str, organic: bool | None = None, search: bool = True) -> list[Candidate]:
        cands: dict[str, Candidate] = {}
        for r in history_matches(self.db, name, limit=6):
            cands[r["id"]] = Candidate(r["id"], r["name"], r["price_cents"], r["unit_quantity"],
                                       bool(r["is_organic"]), r["times"] or 0, r["score"], "historie")
        want_organic = organic if organic is not None else self.organic_level > 0
        if search and self.picnic.is_ready():
            terms = [name]
            if want_organic and not any(c.is_organic for c in cands.values()):
                terms.insert(0, f"biologische {name}")
            for term in terms:
                try:
                    results = self.picnic.search(term, limit=8)
                except Exception:
                    results = []
                for p in results:
                    s = similarity(name, p.name)
                    if s < 0.45:
                        continue
                    if p.id in cands:
                        cands[p.id].on_promo = p.on_promo
                        if p.price_cents:
                            cands[p.id].price_cents = p.price_cents
                        continue
                    cands[p.id] = Candidate(p.id, p.name, p.price_cents, p.unit_quantity, p.is_organic, 0, s,
                                            "picnic", on_promo=p.on_promo)
        ranked = list(cands.values())
        self._score(ranked, organic)
        ranked.sort(key=lambda c: -c.score)
        return ranked

    def _score(self, cands: list[Candidate], organic: bool | None) -> None:
        level = self.organic_level if organic is None else (2 if organic else 0)
        regular = [c.price_cents for c in cands if not c.is_organic and c.price_cents]
        cheapest_regular = min(regular) if regular else None
        for c in cands:
            score = c.similarity
            score += 0.25 * min(c.times_bought, 8) / 8  # vertrouwd product
            if c.is_organic:
                if level >= 2:
                    score += 0.3
                elif level == 1:
                    premium_ok = (not cheapest_regular or not c.price_cents or
                                  c.price_cents <= cheapest_regular * (1 + self.settings.organic_max_premium / 100))
                    score += 0.2 if premium_ok else -0.05
            elif organic is True:
                score -= 0.2
            if c.on_promo:
                score += 0.05
            c.score = round(score, 3)

    def _apply(self, item: dict, c: Candidate) -> None:
        self.db.execute(
            "UPDATE list_items SET product_id = ?, product_name = ?, price_cents = ?, is_organic = ? WHERE id = ?",
            (c.id, c.name, c.price_cents, int(c.is_organic), item["id"]),
        )
        self.db.execute("INSERT INTO products(id, name, unit_quantity, price_cents, is_organic, category, updated_at) "
                        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "price_cents = COALESCE(excluded.price_cents, products.price_cents)",
                        (c.id, c.name, c.unit_quantity, c.price_cents, int(c.is_organic), categorize(c.name), now_iso()))
        self._set_count(self.get(item["id"]))

    def _set_count(self, item: dict) -> None:
        prod = self.db.one("SELECT unit_quantity FROM products WHERE id = ?", (item["product_id"],))
        count = packs_needed(item["quantity"], item["unit"], prod["unit_quantity"] if prod else None)
        self.db.execute("UPDATE list_items SET product_count = ? WHERE id = ?", (count, item["id"]))

    def options(self, item_id: int) -> list[dict]:
        item = self.get(item_id)
        if not item:
            return []
        organic = None if item["is_organic"] is None else bool(item["is_organic"])
        return [c.to_dict() for c in self.candidates(item["name"], organic)[:8]]

    def choose(self, item_id: int, product_id: str) -> dict | None:
        item = self.get(item_id)
        if not item:
            return None
        for c in self.candidates(item["name"], search=True):
            if c.id == product_id:
                self._apply(item, c)
                return self.get(item_id)
        row = self.db.one("SELECT * FROM products WHERE id = ?", (product_id,))
        if row:
            self._apply(item, Candidate(row["id"], row["name"], row["price_cents"], row["unit_quantity"],
                                        bool(row["is_organic"]), 0, 1.0, "historie"))
        else:
            self.db.execute("UPDATE list_items SET product_id = ? WHERE id = ?", (product_id, item_id))
        return self.get(item_id)

    def resolve(self, week: str) -> dict:
        """Koppel alle 'auto'-items zonder product aan het beste Picnic-product."""
        matched, unmatched = 0, []
        for item in self.items(week):
            if item["status"] != "open" or item["mode"] != "auto" or item["product_id"]:
                continue
            organic = None if item["is_organic"] is None else bool(item["is_organic"])
            cands = self.candidates(item["name"], organic)
            if cands and cands[0].similarity >= 0.55:
                self._apply(item, cands[0])
                matched += 1
            else:
                unmatched.append(item["name"])
        return {"matched": matched, "unmatched": unmatched}

    def push_to_cart(self, week: str) -> dict:
        self.resolve(week)
        added, skipped, errors = [], [], []
        for item in self.items(week):
            if item["status"] != "open":
                continue
            if item["mode"] == "zelf":
                skipped.append({"name": item["name"], "reason": "zelf halen"})
                continue
            if not item["product_id"]:
                reason = "kies zelf een product" if item["mode"] == "kiezen" else "geen product gevonden"
                skipped.append({"name": item["name"], "reason": reason})
                continue
            try:
                self.picnic.add_to_cart(item["product_id"], item["product_count"] or 1)
                self.db.execute("UPDATE list_items SET status = 'in_mandje' WHERE id = ?", (item["id"],))
                added.append(item["product_name"] or item["name"])
            except Exception as exc:
                errors.append({"name": item["name"], "error": str(exc)})
        cart = None
        try:
            cart = self.picnic.get_cart().to_dict()
        except Exception:
            pass
        return {"added": added, "skipped": skipped, "errors": errors, "cart": cart}

    def summary(self, week: str) -> dict:
        items = self.items(week)
        priced = [i for i in items if i["price_cents"] and i["mode"] != "zelf"]
        total = sum(i["price_cents"] * (i["product_count"] or 1) for i in priced)
        organic_items = [i for i in priced if i["is_organic"]]
        organic_cost = sum(i["price_cents"] * (i["product_count"] or 1) for i in organic_items)
        return {
            "items": len(items),
            "open": sum(1 for i in items if i["status"] == "open"),
            "in_cart": sum(1 for i in items if i["status"] == "in_mandje"),
            "to_choose": [i["name"] for i in items if i["mode"] == "kiezen" and not i["product_id"]],
            "self_pickup": [i["name"] for i in items if i["mode"] == "zelf"],
            "estimated_cents": total,
            "organic_share": round(len(organic_items) / len(priced), 2) if priced else 0,
            "organic_cost_share": round(organic_cost / total, 2) if total else 0,
        }
