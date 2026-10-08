"""Baby: welke fase, hoe 'met de pot mee', waarschuwingen en allergenen-introductie."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from importlib import resources

from .db import Database
from .text import normalize


@lru_cache
def guide() -> dict:
    raw = resources.files("picnicnow.data").joinpath("baby_guide.json").read_text(encoding="utf-8")
    return json.loads(raw)


def age_months(birthdate: date | None, today: date | None = None) -> int | None:
    if not birthdate:
        return None
    today = today or date.today()
    months = (today.year - birthdate.year) * 12 + (today.month - birthdate.month)
    if today.day < birthdate.day:
        months -= 1
    return max(months, 0)


def stage(birthdate: date | None, today: date | None = None) -> dict:
    """Huidige fase. Zonder geboortedatum gaan we uit van ~8 maanden (prakken)."""
    months = age_months(birthdate, today)
    m = 8 if months is None else months
    for s in guide()["stages"]:
        if s["from"] <= m < s["to"]:
            return {**s, "months": months}
    return {**guide()["stages"][-1], "months": months}


def _rule_hits(text: str, months: int) -> list[dict]:
    n = f" {normalize(text)} "
    hits = []
    for rule in guide()["rules"]:
        if months >= rule["max_months"]:
            continue
        for word in rule["match"]:
            w = normalize(word)
            if f" {w} " in n or (len(w) > 5 and w in n):
                hits.append({"level": rule["level"], "msg": rule["msg"], "trigger": word})
                break
    return hits


def check_ingredients(ingredients: list[dict | str], months: int | None) -> list[dict]:
    """Waarschuwingen voor een lijst ingrediënten, ontdubbeld per boodschap."""
    m = 8 if months is None else months
    seen: dict[str, dict] = {}
    for ing in ingredients:
        name = ing if isinstance(ing, str) else ing.get("name", "")
        for hit in _rule_hits(name, m):
            entry = seen.setdefault(hit["msg"], {**hit, "ingredients": []})
            entry["ingredients"].append(name)
    order = {"verboden": 0, "garen": 1, "apart": 2, "snijden": 3, "beperk": 4, "info": 5}
    return sorted(seen.values(), key=lambda h: order.get(h["level"], 9))


def adapt_meal(meal: dict, birthdate: date | None, today: date | None = None) -> dict:
    """Hoe laat je baby met dit gerecht meeëten?"""
    st = stage(birthdate, today)
    months = st["months"]
    baby = meal.get("baby") or {}
    from_months = baby.get("from_months", 6)
    warnings = check_ingredients(meal.get("ingredients", []), months)
    if st["key"] in ("melk",):
        verdict = "nog niet"
    elif months is not None and months < from_months:
        verdict = f"vanaf ~{from_months} mnd"
    elif any(w["level"] == "verboden" for w in warnings):
        verdict = "deels (zie waarschuwing)"
    else:
        verdict = "ja"
    steps = []
    if baby.get("take_out_before"):
        steps.append(f"Haal baby's portie eruit vóór: {baby['take_out_before']}.")
    steps.append(f"Textuur nu: {st['texture'].lower()} ({st['portion']}).")
    if baby.get("tip"):
        steps.append(baby["tip"])
    return {"meal": meal.get("name"), "stage": st, "verdict": verdict, "steps": steps, "warnings": warnings}


def ideas(birthdate: date | None, today: date | None = None) -> dict:
    st = stage(birthdate, today)
    return {"stage": st, "ideas": guide()["ideas"].get(st["key"], {})}


# --- allergenen-tracker -------------------------------------------------------

def allergen_status(db: Database, birthdate: date | None, today: date | None = None) -> list[dict]:
    months = age_months(birthdate, today)
    tried = {r["name"]: r for r in db.query("SELECT * FROM baby_foods WHERE is_allergen = 1")}
    out = []
    for a in guide()["allergens"]:
        row = tried.get(a["name"])
        status = "geïntroduceerd" if row and row["times_given"] >= 3 else ("gestart" if row else "nog niet")
        urgent = a["name"] in ("pinda", "ei") and status == "nog niet" and months is not None and 4 <= months < 8
        out.append({**a, "status": status, "times_given": row["times_given"] if row else 0,
                    "last_given": row["last_given"] if row else None,
                    "reaction": row["reaction"] if row else None, "urgent": urgent})
    return out


def log_food(db: Database, name: str, reaction: str | None = None) -> dict:
    allergen_names = {a["name"] for a in guide()["allergens"]}
    is_allergen = int(name in allergen_names)
    today = date.today().isoformat()
    db.execute(
        "INSERT INTO baby_foods(name, is_allergen, first_tried, last_given, times_given, reaction) "
        "VALUES (?,?,?,?,1,?) ON CONFLICT(name) DO UPDATE SET last_given = excluded.last_given, "
        "times_given = baby_foods.times_given + 1, reaction = COALESCE(excluded.reaction, baby_foods.reaction)",
        (name, is_allergen, today, today, reaction),
    )
    return db.one("SELECT * FROM baby_foods WHERE name = ?", (name,)) or {}


def foods_tried(db: Database) -> list[dict]:
    return db.query("SELECT * FROM baby_foods ORDER BY last_given DESC")

