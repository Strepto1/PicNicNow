from datetime import date

from picnicnow import baby, combine, meals
from picnicnow.config import Settings
from picnicnow.core import App
from picnicnow.db import Database
from picnicnow.picnic import DemoPicnic

PROFILE = {"name": "Noor", "spice_ok": True, "likes": "kerrie, knoflook, rijst, pasta, tomaat, prei, mediterraan",
           "avg": "af en toe"}


def test_age_months_becomes_birthdate():
    a = App(Settings(baby_age_months=9), Database(), DemoPicnic())
    assert a.baby_months in (8, 9) and a.profile()["baby_birthdate"]


def test_spice_ok_keeps_mild_spices(app):
    m = meals.get_meal(app.db, "Kip tandoori met bloemkoolrijst en raita")
    a = baby.adapt_meal(m, date(2026, 1, 1), date(2026, 10, 8), PROFILE)
    assert "mix met zout" in a["steps"][0]
    assert any("Milde kruiden" in s for s in a["steps"])
    chili = baby.check_ingredients(["chilivlokken"], 9, spice_ok=True)
    assert chili[0]["level"] == "info"
    assert baby.check_ingredients(["chilivlokken"], 9)[0]["level"] == "apart"
    # zout blijft altijd eruit
    assert baby.check_ingredients(["zout"], 9, spice_ok=True)[0]["level"] == "apart"


def test_adventurous_ideas_first():
    ideas = baby.ideas(date(2026, 1, 1), date(2026, 10, 8), PROFILE)["ideas"]
    assert "kerrie" in ideas["lunch"][0]


def test_suggest_likes_and_avg_once(app, week):
    sug = meals.suggest(app.db, week, count=8, baby_months=9, baby_profile=PROFILE)
    assert sum("avg" in s["tags"] for s in sug) == 1
    assert any(r.startswith("Noor lust") for s in sug for r in s["reasons"])


def test_avg_tip_when_week_has_none(app, week):
    for d, n in [("ma", "Spaghetti bolognese met verstopte groenten"), ("di", "Linzendahl met spinazie en naan"),
                 ("wo", "Chili sin carne met zoete aardappel"), ("do", "Kip-kerrie met rijst en sperziebonen")]:
        meals.plan_set(app.db, week, d, meal_id=meals.get_meal(app.db, n)["id"])
    tips = combine.combine_week(app.db, week, 9, PROFILE)
    assert any("aardappel-groente-vlees" in t["title"] for t in tips)
