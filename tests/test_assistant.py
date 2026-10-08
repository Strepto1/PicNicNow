import json

from anthropic.types.beta import BetaMessage

from picnicnow.assistant import TOOLS, Assistant, Tools, build_system, repair_tool_pairs


def _msg(content, stop):
    return BetaMessage.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                       "content": content, "stop_reason": stop, "stop_sequence": None,
                                       "usage": {"input_tokens": 1, "output_tokens": 1}})


class FakeClient:
    def __init__(self, script):
        self.calls = []
        client = self

        class Messages:
            def create(self, **kw):
                client.calls.append(json.loads(json.dumps(kw, default=str)))
                return script[len(client.calls) - 1]

        class Beta:
            messages = Messages()

        self.beta = Beta()


def test_tool_loop_and_history_replay(app, week):
    app.settings.anthropic_api_key = "x"
    client = FakeClient([
        _msg([{"type": "thinking", "thinking": "", "signature": "s"},
              {"type": "tool_use", "id": "a", "name": "plan_gerecht",
               "input": {"dag": "vr", "gerecht": "Shakshuka met feta en brood"}},
              {"type": "tool_use", "id": "b", "name": "zet_op_lijst",
               "input": {"items": [{"naam": "avocado", "modus": "zelf"}]}}], "tool_use"),
        _msg([{"type": "text", "text": "Staat erop!"}], "end_turn"),
        _msg([{"type": "text", "text": "Graag gedaan."}], "end_turn"),
    ])
    bot = Assistant(app, client=client)
    bot.note(week, "Sanne", "eieren zijn op")
    res = bot.respond(week, "Kevin", "Vrijdag shakshuka?")
    assert res == {"reply": "Staat erop!", "changed": True}
    second = client.calls[1]
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["user", "user", "assistant", "user"]
    assert second["messages"][2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "s"}
    results = second["messages"][3]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"] and not any(r.get("is_error") for r in results)
    assert second["fallbacks"] == "default" and second["model"] == "claude-opus-5-5"
    assert any(i["name"] == "avocado" and i["mode"] == "zelf" for i in app.shopping.items(week))
    # tweede beurt bouwt append-only voort op dezelfde geschiedenis
    bot.respond(week, "Sanne", "Dank je")
    third = client.calls[2]["messages"]
    assert third[:4] == second["messages"]
    # UI-geschiedenis bevat alleen leesbare berichten
    shown = [m["display"] for m in bot.history(bot.conversation_id(week))]
    assert shown == ["eieren zijn op", "Vrijdag shakshuka?", "Staat erop!", "Dank je", "Graag gedaan."]


def test_unknown_meal_needs_ingredients(app, week):
    tools = Tools(app, week)
    try:
        tools.run("plan_gerecht", {"dag": "za", "gerecht": "Oma's soep"})
        raise AssertionError("verwacht ToolError")
    except Exception as exc:
        assert "nieuw_gerecht" in str(exc)
    out = tools.run("plan_gerecht", {"dag": "za", "gerecht": "Oma's soep", "nieuw_gerecht": {
        "ingredienten": [{"name": "soepgroente", "qty": 500, "unit": "g"}, {"name": "zout", "pantry": True}]}})
    assert out["op_lijst"] == ["soepgroente"]
    assert tools.run("plan_gerecht", {"dag": "zo", "gerecht": "uit eten", "ingredienten_op_lijst": False})


def test_all_tools_have_handlers(app, week):
    tools = Tools(app, week)
    for t in TOOLS:
        assert hasattr(tools, f"t_{t['name']}"), t["name"]
        assert t["input_schema"]["type"] == "object"


def test_read_only_tools_run(app, week):
    tools = Tools(app, week)
    for name in ("week_overzicht", "gerecht_suggesties", "kant_en_klaar_opties", "combineer_tips", "baby_advies",
                 "allergenen", "vaste_boodschappen", "aanbiedingen"):
        json.dumps(tools.run(name, {}), default=str)
    assert tools.run("zoek_product", {"term": "melk"})["opties"]


def test_repair_tool_pairs():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hoi"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}]}]
    fixed = repair_tool_pairs(msgs)
    assert fixed[-1]["content"][0]["tool_use_id"] == "x" and fixed[-1]["content"][0]["is_error"]


def test_system_prompt_mentions_household(app):
    s = build_system(app)
    assert "Kevin en Sanne" in s and "biologisch" in s


def test_haiku_has_no_fallbacks_and_usage_is_recorded(app, week):
    from picnicnow import usage

    app.settings.anthropic_api_key = "x"
    app.update_profile(model="claude-haiku-5-5")
    client = FakeClient([_msg([{"type": "text", "text": "Hoi!"}], "end_turn")])
    Assistant(app, client=client).respond(week, "Kevin", "hoi")
    assert "fallbacks" not in client.calls[0] and client.calls[0]["model"] == "claude-haiku-5-5"
    assert usage.month_summary(app.db)["calls"] == 1
