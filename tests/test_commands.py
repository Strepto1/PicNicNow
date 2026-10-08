import pytest

from picnicnow.commands import Commands, parse_items


@pytest.fixture
def cmd(app):
    return Commands(app)


def test_parse_items():
    items = parse_items("melk, 2 komkommers, een pak biologische yoghurt en 500 gram gehakt")
    assert [(i.name, i.quantity, i.unit, i.organic) for i in items] == [
        ("melk", 1, None, None), ("komkommers", 2, None, None), ("yoghurt", 1, "pak", True), ("gehakt", 500, "g", None)]
    assert parse_items("avocado zelf halen")[0].mode == "zelf"


@pytest.mark.parametrize("text,expect", [
    ("Zet melk en eieren op de lijst", ["melk", "eieren"]),
    ("De pindakaas is op.", ["pindakaas"]),
    ("We hebben nog kipfilet nodig", ["kipfilet"]),
    ("en nog brood erbij", ["brood"]),
])
def test_add_phrases(cmd, app, week, text, expect):
    r = cmd.handle(week, "Kevin", text)
    assert r and r.changed
    names = [i["name"] for i in app.shopping.items(week)]
    assert all(e in names for e in expect)


def test_remove_and_self(cmd, app, week):
    cmd.handle(week, "Kevin", "zet melk op de lijst")
    assert "Van de lijst: melk" in cmd.handle(week, "Kevin", "haal de melk van de lijst").text
    cmd.handle(week, "Kevin", "avocado zelf halen")
    assert app.shopping.find(week, "avocado")["mode"] == "zelf"


def test_plan_known_and_unknown(cmd, app, week):
    r = cmd.handle(week, "Sanne", "woensdag shakshuka")
    assert "Shakshuka met feta en brood" in r.text and "🍼" in r.text
    assert any(i["name"] == "eieren" for i in app.shopping.items(week))
    r = cmd.handle(week, "Sanne", "vrijdag eten we pizza")
    assert "ken ik niet" in r.text
    r = cmd.handle(week, "Sanne", "pasta pesto op dinsdag")
    assert "Pasta pesto" in r.text


def test_suggestions_and_levels(cmd, week):
    assert "niveau 4" in cmd.handle(week, None, "iets nieuws?").text
    assert "niveau 3" in cmd.handle(week, None, "kant-en-klaar ideeën").text
    assert "Ideeën voor maandag" in cmd.handle(week, None, "wat eten we maandag, iets nieuws").text
    assert "waarschijnlijk" in cmd.handle(week, None, "iets vertrouwds?").text  # nog niets gemarkeerd


def test_small_talk_is_ignored(cmd, week):
    for t in ["hoe was je dag?", "ik heb eigenlijk geen zin om te koken", "lekker weertje"]:
        assert cmd.handle(week, "Kevin", t) is None


def test_mode_rule_compound_head(app, week):
    assert app.shopping.add(week, "trostomaten")["mode"] == "kiezen"
    assert app.shopping.add(week, "tomatenblokjes")["mode"] == "auto"
