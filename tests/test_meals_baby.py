from datetime import date

from picnicnow import baby, combine, meals


def test_levels(app):
    m = meals.get_meal(app.db, "Pasta pesto met broccoli en kip")
    assert m["level"] == 4
    assert meals.set_familiarity(app.db, m["id"], "vaak")["level"] == 1
    assert meals.set_familiarity(app.db, m["id"], "soms")["level"] == 2
    assert meals.get_meal(app.db, "Beef rendang met atjar")["level"] == 5
    ready = meals.ready_meals(app.db, app.picnic)
    assert ready and all(meals.get_meal(app.db, r["meal_id"])["level"] == 3 for r in ready)


def test_cooked_increments_familiarity(app, week):
    m = meals.get_meal(app.db, "Shakshuka met feta en brood")
    meals.plan_set(app.db, week, "ma", meal_id=m["id"])
    meals.mark_cooked(app.db, week, "ma")
    assert meals.get_meal(app.db, m["id"])["level"] == 2


def test_suggest_prefers_leftovers_and_skips_planned(app, week):
    bol = meals.get_meal(app.db, "Spaghetti bolognese met verstopte groenten")
    meals.plan_set(app.db, week, "ma", meal_id=bol["id"])
    sug = meals.suggest(app.db, week, count=5)
    assert bol["id"] not in [s["id"] for s in sug]
    assert sug[0]["name"].startswith("Lasagne")  # gebruikt restje bolognesesaus


def test_likely_known_from_history(app):
    known = meals.likely_known(app.db)
    assert known and known[0]["coverage"] >= 0.6


def test_combine_tips(app, week):
    for day, name in [("ma", "Zalm uit de oven met krieltjes en broccoli"), ("di", "Pasta pesto met broccoli en kip"),
                      ("wo", "Chili sin carne met zoete aardappel")]:
        meals.plan_set(app.db, week, day, meal_id=meals.get_meal(app.db, name)["id"])
    tips = combine.combine_week(app.db, week, baby_months=9)
    types = {t["type"] for t in tips}
    assert {"samen", "dubbel", "baby"} <= types
    assert any("roccoli" in t["title"] for t in tips if t["type"] == "samen")


def test_baby_stage_and_rules():
    today = date(2026, 10, 8)
    assert baby.stage(date(2026, 7, 1), today)["key"] == "melk"
    assert baby.stage(date(2026, 5, 1), today)["key"] == "oefenhapjes"
    st = baby.stage(date(2025, 12, 1), today)
    assert st["months"] == 10 and st["key"] == "meeeten"
    warnings = baby.check_ingredients(["honing", "walnoten", "zout en peper"], months=9)
    levels = [w["level"] for w in warnings]
    assert levels[0] == "verboden" and "apart" in levels
    assert not baby.check_ingredients(["honing"], months=14)  # na 1 jaar oké


def test_adapt_meal(app):
    m = meals.get_meal(app.db, "Nasi goreng met ei en komkommer")
    a = baby.adapt_meal(m, date(2026, 1, 1), today=date(2026, 10, 8))
    assert a["verdict"] == "ja"
    assert any("ketjap" in s for s in a["steps"])
    assert any("doorgaren" in w["msg"] for w in a["warnings"])


def test_allergen_tracker(app):
    bd = date(2026, 3, 1)
    status = {a["name"]: a for a in baby.allergen_status(app.db, bd, today=date(2026, 9, 1))}
    assert status["pinda"]["urgent"]
    for _ in range(3):
        baby.log_food(app.db, "pinda")
    status = {a["name"]: a for a in baby.allergen_status(app.db, bd, today=date(2026, 9, 1))}
    assert status["pinda"]["status"] == "geïntroduceerd" and not status["pinda"]["urgent"]
