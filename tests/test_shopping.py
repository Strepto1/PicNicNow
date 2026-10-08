from picnicnow import meals
from picnicnow.shopping import packs_needed, parse_amount


def test_parse_and_packs():
    assert parse_amount("1,5 kg") == (1500, "g")
    assert parse_amount("4x56 stuks") == (224, "stuk")
    assert packs_needed(800, "g", "1,5 kg") == 1
    assert packs_needed(300, "g", "250 g") == 2
    assert packs_needed(520, "g", "500 g") == 1   # kleine marge
    assert packs_needed(4, "stuk", "6 stuks") == 1
    assert packs_needed(2, "teen", "3 stuks") == 1


def test_add_merges_and_prefers_history_and_organic(app, week):
    sl = app.shopping
    sl.add(week, "kipfilet", 250, "g")
    sl.add(week, "kipfilet", 250, "g")
    items = [i for i in sl.items(week) if i["name"] == "kipfilet"]
    assert len(items) == 1 and items[0]["quantity"] == 500
    sl.resolve(week)
    item = sl.find(week, "kipfilet")
    assert item["product_name"] == "Biologische kipfilet"  # bio + eerder gekocht
    assert item["product_count"] == 2                       # 500 g bij pakken van 400 g
    # zonder bio-voorkeur wint het vaker gekochte/goedkopere product niet per se, maar bio mag niet meer winnen
    app.update_profile(organic_level=0)
    cands = sl.candidates("kipfilet", organic=False)
    assert not cands[0].is_organic


def test_mode_rules(app, week):
    sl = app.shopping
    assert sl.add(week, "trostomaten")["mode"] == "kiezen"       # standaardregel 'tomaat'
    sl.set_mode_rule("avocado", "zelf")
    assert sl.add(week, "avocado's", 2)["mode"] == "zelf"
    assert sl.add(week, "komkommer")["mode"] == "auto"


def test_push_to_cart_skips_self_and_unchosen(app, week):
    sl = app.shopping
    sl.add(week, "bananen")
    sl.add(week, "trostomaten")            # kiezen, nog geen keuze
    sl.add(week, "avocado", mode="zelf")
    res = sl.push_to_cart(week)
    assert any("bananen" in a.lower() for a in res["added"])
    reasons = {s["name"]: s["reason"] for s in res["skipped"]}
    assert reasons["trostomaten"] == "kies zelf een product"
    assert reasons["avocado"] == "zelf halen"
    assert res["cart"]["lines"]


def test_choose_option(app, week):
    sl = app.shopping
    item = sl.add(week, "trostomaten")
    opts = sl.options(item["id"])
    assert opts and all("tomat" in o["name"].lower() for o in opts[:3])
    chosen = sl.choose(item["id"], opts[1]["id"])
    assert chosen["product_id"] == opts[1]["id"]


def test_basics_not_duplicated(app, week):
    meal = meals.get_meal(app.db, "Spaghetti bolognese met verstopte groenten")
    app.shopping.add_meal(week, meal)
    app.shopping.add_basics(week)
    names = [i["name"].lower() for i in app.shopping.items(week)]
    assert not ("wortelen" in names and "biologische wortelen" in names)


def test_meal_scaling_and_pantry(app, week):
    meal = meals.get_meal(app.db, "Spaghetti bolognese met verstopte groenten")
    added = app.shopping.add_meal(week, meal, servings=4)
    gehakt = next(i for i in added if i["name"] == "rundergehakt")
    assert gehakt["quantity"] == 600
    assert "italiaanse kruiden" not in [i["name"] for i in added]
    assert "olijfolie" in app.shopping.pantry_check(meal)
