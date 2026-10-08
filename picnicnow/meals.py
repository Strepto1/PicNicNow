"""Gerechten: bibliotheek, intensiteitsniveaus 1–5, suggesties en het weekmenu.

Niveaus
  1  Vertrouwd           – gerechten die jullie vaak maken
  2  Bekend               – al eens gemaakt / variatie op iets bekends
  3  Kant-en-klaar        – verse maaltijden en maaltijdpakketten van Picnic
  4  Nieuw & haalbaar     – nieuw, maar prima doordeweeks
  5  Nieuw & uitdagend    – nieuw en wat ingewikkelder (weekend)
"""

from __future__ import annotations

import json
from datetime import timedelta
from importlib import resources

from .db import Database
from .text import DAYS, normalize, similarity, week_monday

LEVELS = {
    1: "Vertrouwd",
    2: "Bekend",
    3: "Kant-en-klaar",
    4: "Nieuw & haalbaar",
    5: "Nieuw & uitdagend",
}

READY_MARKERS = ("maaltijd", "maaltijdpakket", "pakket", "ovenschotel", "kant-en-klaar", "kant en klaar")
READY_DISHES = ("lasagne", "pizza", "soep", "stamppot", "curry", "macaroni", "nasi", "bami", "risotto")

# Ingrediënten die snel bederven of in een te grote verpakking komen (restjes!)
PERISHABLE_HINTS = (
    "koriander", "basilicum", "peterselie", "munt", "dille", "bieslook", "salie", "bosui",
    "spinazie", "paksoi", "andijvie", "sla", "rucola", "champignon", "shiitake", "courgette",
    "paprika", "broccoli", "bloemkool", "pompoen", "avocado", "komkommer", "prei", "sperziebonen",
    "creme fraiche", "crème fraîche", "kookroom", "ricotta", "mozzarella", "feta", "geitenkaas",
    "kokosmelk", "yoghurt", "kwark", "zalm", "kabeljauw", "kip", "gehakt", "garnalen", "tofu",
    "limoen", "citroen", "gember", "citroengras", "venkel",
)


# ---------------------------------------------------------------------------
# Bibliotheek
# ---------------------------------------------------------------------------
def load_recipes() -> list[dict]:
    raw = resources.files("picnicnow.data").joinpath("recipes.json").read_text(encoding="utf-8")
    return json.loads(raw)


def seed_recipes(db: Database) -> int:
    added = 0
    for r in load_recipes():
        exists = db.one("SELECT id FROM meals WHERE name = ?", (r["name"],))
        if exists:
            continue
        db.execute(
            "INSERT INTO meals(name, kind, difficulty, familiarity, ingredients, tags, prep_minutes, baby, source, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["name"], r.get("kind", "zelf"), r.get("difficulty", 4), "nieuw",
             json.dumps(r["ingredients"], ensure_ascii=False), json.dumps(r.get("tags", []), ensure_ascii=False),
             r.get("prep_minutes"), json.dumps({**r.get("baby", {}), "makes_extra": r.get("makes_extra"),
                                                "uses_leftover": r.get("uses_leftover"),
                                                "servings": r.get("servings", 2)}, ensure_ascii=False),
             "bibliotheek", None),
        )
        added += 1
    return added


def meal_level(m: dict) -> int:
    if m.get("kind") == "kant-en-klaar":
        return 3
    fam = m.get("familiarity") or "nieuw"
    times = m.get("times_cooked") or 0
    if fam == "vaak" or times >= 4:
        return 1
    if fam == "soms" or times >= 1:
        return 2
    return 5 if (m.get("difficulty") or 4) >= 5 else 4


def _hydrate(row: dict | None) -> dict | None:
    if not row:
        return None
    m = dict(row)
    m["ingredients"] = json.loads(m.get("ingredients") or "[]")
    m["tags"] = json.loads(m.get("tags") or "[]")
    extra = json.loads(m.get("baby") or "{}") if m.get("baby") else {}
    m["makes_extra"] = extra.pop("makes_extra", None)
    m["uses_leftover"] = extra.pop("uses_leftover", None)
    m["servings"] = extra.pop("servings", 2)
    m["baby"] = extra
    m["level"] = meal_level(m)
    m["level_label"] = LEVELS[m["level"]]
    return m


def get_meal(db: Database, ref: int | str) -> dict | None:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return _hydrate(db.one("SELECT * FROM meals WHERE id = ?", (int(ref),)))
    row = db.one("SELECT * FROM meals WHERE name = ? COLLATE NOCASE", (ref,))
    if row:
        return _hydrate(row)
    # fuzzy
    best, best_s = None, 0.0
    for r in db.query("SELECT * FROM meals"):
        s = similarity(ref, r["name"])
        if s > best_s:
            best, best_s = r, s
    return _hydrate(best) if best_s >= 0.6 else None


def list_meals(db: Database, level: int | None = None, tag: str | None = None) -> list[dict]:
    meals = [_hydrate(r) for r in db.query("SELECT * FROM meals ORDER BY name")]
    if level:
        meals = [m for m in meals if m["level"] == level]
    if tag:
        meals = [m for m in meals if tag in m["tags"]]
    return meals


def upsert_meal(db: Database, name: str, ingredients: list[dict] | None = None, *, kind: str = "zelf",
                difficulty: int = 4, familiarity: str | None = None, tags: list[str] | None = None,
                prep_minutes: int | None = None, baby: dict | None = None, picnic_product_id: str | None = None,
                source: str = "gesprek", notes: str | None = None) -> dict:
    existing = db.one("SELECT * FROM meals WHERE name = ? COLLATE NOCASE", (name,))
    if existing:
        sets, params = [], []
        if ingredients is not None:
            sets.append("ingredients = ?"); params.append(json.dumps(ingredients, ensure_ascii=False))
        if familiarity:
            sets.append("familiarity = ?"); params.append(familiarity)
        if tags is not None:
            sets.append("tags = ?"); params.append(json.dumps(tags, ensure_ascii=False))
        if notes:
            sets.append("notes = ?"); params.append(notes)
        if picnic_product_id:
            sets.append("picnic_product_id = ?"); params.append(picnic_product_id)
        if sets:
            db.execute(f"UPDATE meals SET {', '.join(sets)} WHERE id = ?", (*params, existing["id"]))
        return get_meal(db, existing["id"])
    mid = db.execute(
        "INSERT INTO meals(name, kind, difficulty, familiarity, ingredients, tags, prep_minutes, baby, "
        "picnic_product_id, source, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, kind, difficulty, familiarity or "nieuw", json.dumps(ingredients or [], ensure_ascii=False),
         json.dumps(tags or [], ensure_ascii=False), prep_minutes, json.dumps(baby or {}, ensure_ascii=False),
         picnic_product_id, source, notes),
    )
    return get_meal(db, mid)


def set_familiarity(db: Database, ref: int | str, familiarity: str) -> dict | None:
    m = get_meal(db, ref)
    if not m or familiarity not in ("vaak", "soms", "nieuw"):
        return None
    db.execute("UPDATE meals SET familiarity = ? WHERE id = ?", (familiarity, m["id"]))
    return get_meal(db, m["id"])


def likely_known(db: Database, min_coverage: float = 0.6, limit: int = 12) -> list[dict]:
    """Bibliotheekgerechten waarvan jullie de verse ingrediënten al vaak bestellen."""
    bought = db.query(
        "SELECT p.name, COUNT(*) AS times FROM purchases pu JOIN products p ON p.id = pu.product_id "
        "GROUP BY p.id HAVING COUNT(*) >= 2"
    )
    if not bought:
        return []
    out = []
    for m in list_meals(db):
        if m["kind"] != "zelf" or m["familiarity"] != "nieuw":
            continue
        fresh = [i["name"] for i in m["ingredients"] if not i.get("pantry")]
        if not fresh:
            continue
        hit = [i for i in fresh if any(similarity(i, b["name"]) >= 0.6 for b in bought)]
        cov = len(hit) / len(fresh)
        if cov >= min_coverage:
            out.append({"meal": m["name"], "id": m["id"], "coverage": round(cov, 2), "matched": hit})
    out.sort(key=lambda x: -x["coverage"])
    return out[:limit]


# ---------------------------------------------------------------------------
# Kant-en-klaar (niveau 3)
# ---------------------------------------------------------------------------
def looks_ready_made(name: str) -> bool:
    n = normalize(name)
    if any(m in n for m in READY_MARKERS):
        return True
    return ("verse" in n or "picnic" in n) and any(d in n for d in READY_DISHES)


def ready_meals(db: Database, picnic=None, search_terms: tuple[str, ...] = ("verse maaltijd", "maaltijdpakket", "ovenschotel")) -> list[dict]:
    """Kant-en-klare opties: eerst wat jullie al eens kochten, daarna Picnic-aanbod."""
    out: dict[str, dict] = {}
    for p in db.query(
        "SELECT p.*, COUNT(pu.delivery_id) AS times FROM products p "
        "LEFT JOIN purchases pu ON pu.product_id = p.id GROUP BY p.id ORDER BY times DESC"
    ):
        if looks_ready_made(p["name"]):
            out[p["id"]] = {"product_id": p["id"], "name": p["name"], "price_cents": p["price_cents"],
                            "is_organic": bool(p["is_organic"]), "times_bought": p["times"], "source": "eerder besteld"}
    if picnic is not None and picnic.is_ready():
        for term in search_terms:
            try:
                for item in picnic.search(term, limit=10):
                    if item.id not in out and looks_ready_made(item.name):
                        db.execute(
                            "INSERT INTO products(id, name, unit_quantity, price_cents, image_id, is_organic, category) "
                            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                            (item.id, item.name, item.unit_quantity, item.price_cents, item.image_id,
                             int(item.is_organic), "Kant-en-klaar"))
                        out[item.id] = {"product_id": item.id, "name": item.name, "price_cents": item.price_cents,
                                        "is_organic": item.is_organic, "times_bought": 0, "source": "Picnic-aanbod"}
            except Exception:  # zoeken is 'nice to have'
                continue
    # registreer als gerecht op niveau 3, zodat ze in het weekmenu kunnen
    for r in out.values():
        m = upsert_meal(db, r["name"], [{"name": r["name"], "qty": 1, "unit": "stuk"}], kind="kant-en-klaar",
                        difficulty=3, picnic_product_id=r["product_id"], source=r["source"], tags=["kant-en-klaar"])
        r["meal_id"] = m["id"]
    return sorted(out.values(), key=lambda r: (-r["times_bought"], not r["is_organic"], r["price_cents"] or 0))


# ---------------------------------------------------------------------------
# Weekmenu
# ---------------------------------------------------------------------------
def plan_get(db: Database, week: str) -> list[dict]:
    rows = db.query("SELECT * FROM plan WHERE week = ?", (week,))
    for r in rows:
        r["meal"] = get_meal(db, r["meal_id"]) if r["meal_id"] else None
    rows.sort(key=lambda r: (DAYS.index(r["day"]) if r["day"] in DAYS else 9, r["slot"]))
    return rows


def plan_set(db: Database, week: str, day: str, slot: str = "diner", meal_id: int | None = None,
             title: str | None = None, servings: int | None = None, note: str | None = None) -> dict:
    if day not in DAYS:
        raise ValueError(f"Onbekende dag '{day}', gebruik een van {DAYS}")
    db.execute(
        "INSERT INTO plan(week, day, slot, meal_id, title, servings, note) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(week, day, slot) DO UPDATE SET meal_id=excluded.meal_id, title=excluded.title, "
        "servings=excluded.servings, note=excluded.note, cooked=0",
        (week, day, slot, meal_id, title, servings, note),
    )
    return next(r for r in plan_get(db, week) if r["day"] == day and r["slot"] == slot)


def plan_clear(db: Database, week: str, day: str, slot: str = "diner") -> None:
    db.execute("DELETE FROM plan WHERE week = ? AND day = ? AND slot = ?", (week, day, slot))


def mark_cooked(db: Database, week: str, day: str, slot: str = "diner") -> dict | None:
    row = db.one("SELECT * FROM plan WHERE week = ? AND day = ? AND slot = ?", (week, day, slot))
    if not row:
        return None
    db.execute("UPDATE plan SET cooked = 1 WHERE week = ? AND day = ? AND slot = ?", (week, day, slot))
    if row["meal_id"]:
        cooked_on = (week_monday(week) + timedelta(days=DAYS.index(day))).isoformat()
        db.execute("UPDATE meals SET times_cooked = times_cooked + 1, last_cooked = ? WHERE id = ?",
                   (cooked_on, row["meal_id"]))
        return get_meal(db, row["meal_id"])
    return None


def recent_meal_ids(db: Database, week: str, days: int = 14) -> set[int]:
    """Gerechten die in de afgelopen weken al op het menu stonden of gekookt zijn."""
    monday = week_monday(week)
    since = (monday - timedelta(days=days)).isoformat()
    recent = {r["id"] for r in db.query("SELECT id FROM meals WHERE last_cooked >= ?", (since,))}
    for r in db.query("SELECT week, meal_id FROM plan WHERE meal_id IS NOT NULL AND week != ?", (week,)):
        try:
            if 0 < (monday - week_monday(r["week"])).days <= days:
                recent.add(r["meal_id"])
        except ValueError:
            continue
    return recent


LEFTOVER_PRONE = (
    ("koriander", "basilicum", "peterselie", "munt", "dille", "salie", "bieslook", "bosui", "citroengras"),
    ("creme fraiche", "kookroom", "ricotta", "kokosmelk", "mozzarella", "feta", "geitenkaas", "yoghurt"),
    ("spinazie", "paksoi", "andijvie", "sla", "rucola", "courgette", "paprika", "bloemkool", "pompoen",
     "prei", "venkel", "champignon", "shiitake", "komkommer", "avocado", "gember", "limoen", "citroen"),
)


def leftover_rank(name: str) -> int:
    n = normalize(name)
    for rank, group in enumerate(LEFTOVER_PRONE):
        if any(w in n for w in group):
            return rank
    return 9


def is_perishable(name: str) -> bool:
    n = normalize(name)
    return any(h in n for h in (normalize(x) for x in PERISHABLE_HINTS))


def week_ingredients(db: Database, week: str) -> list[str]:
    names = []
    for row in plan_get(db, week):
        if row["meal"]:
            names += [i["name"] for i in row["meal"]["ingredients"] if not i.get("pantry")]
    return names


def suggest(db: Database, week: str, *, level: int | None = None, count: int = 5, tags: list[str] | None = None,
            max_minutes: int | None = None, baby_months: int | None = None, query: str | None = None) -> list[dict]:
    """Gerechtsuggesties met uitleg waarom (restjes-match, variatie, baby, bekendheid)."""
    planned = plan_get(db, week)
    planned_ids = {r["meal_id"] for r in planned if r["meal_id"]}
    recent = recent_meal_ids(db, week)
    planned_tags = [t for r in planned if r["meal"] for t in r["meal"]["tags"]]
    # vlees/vis koop je per portie; restjes ontstaan bij kruiden, zuivel en groente
    fresh_this_week = {n for n in week_ingredients(db, week) if leftover_rank(n) < 9}
    leftovers = {r["meal"]["makes_extra"] for r in planned if r["meal"] and r["meal"].get("makes_extra")}

    out = []
    for m in list_meals(db, level=level):
        if m["id"] in planned_ids:
            continue
        if tags and not set(tags) & set(m["tags"]):
            continue
        if max_minutes and (m.get("prep_minutes") or 0) > max_minutes:
            continue
        if query and similarity(query, m["name"] + " " + " ".join(m["tags"])) < 0.45:
            continue
        score, reasons = 1.0, []
        if m["id"] in recent:
            score -= 0.8
            reasons.append("pas nog gegeten")
        shared = sorted({i["name"] for i in m["ingredients"]
                         if any(similarity(i["name"], f) >= 0.7 for f in fresh_this_week)})
        if shared:
            score += 0.35 * len(shared)
            reasons.append("gebruikt restjes van: " + ", ".join(shared[:3]))
        if m.get("uses_leftover") and m["uses_leftover"] in leftovers:
            score += 0.8
            reasons.append(f"maakt slim gebruik van restje {m['uses_leftover']}")
        repeated = sum(1 for t in m["tags"] if planned_tags.count(t) >= 2 and t not in ("snel",))
        if repeated:
            score -= 0.3 * repeated
            reasons.append("lijkt op wat er al op het menu staat")
        if baby_months is not None and m.get("baby"):
            if baby_months >= (m["baby"].get("from_months") or 6):
                score += 0.3
                reasons.append("baby kan mee-eten")
        if m["level"] == 1:
            reasons.append(f"{m['times_cooked'] or 'vaak'}x gemaakt" if m["times_cooked"] else "vertrouwd")
        if "vega" in m["tags"]:
            score += 0.1
        out.append({"id": m["id"], "name": m["name"], "level": m["level"], "level_label": m["level_label"],
                    "prep_minutes": m.get("prep_minutes"), "tags": m["tags"], "score": round(score, 2),
                    "reasons": reasons, "kind": m["kind"]})
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out[:count]


def scale_ingredient(ing: dict, factor: float) -> dict:
    out = dict(ing)
    if isinstance(ing.get("qty"), (int, float)):
        q = ing["qty"] * factor
        out["qty"] = round(q) if q >= 10 else round(q, 1)
    return out
