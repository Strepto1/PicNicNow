from fastapi.testclient import TestClient

from picnicnow.server import create_app


def test_endpoints_and_live_updates(app):
    client = TestClient(create_app(app))
    assert client.get("/").status_code == 200
    state = client.get("/api/state").json()
    assert state["demo"] and state["assistant"]["mode"] == "commando"
    with client.websocket_connect("/ws?name=Sanne") as ws:
        assert ws.receive_json()["type"] == "presence"
        plan = client.post("/api/plan", json={"day": "ma", "meal_id": 1}).json()
        assert plan[0]["day"] == "ma"
        assert ws.receive_json()["type"] == "state"
        item = client.post("/api/list", json={"name": "trostomaten"}).json()
        assert ws.receive_json()["type"] == "state"
    assert item["mode"] == "kiezen"
    opts = client.get(f"/api/list/{item['id']}/options").json()
    assert client.post(f"/api/list/{item['id']}/choose", json={"product_id": opts[0]["id"]}).json()["product_id"]
    assert client.patch(f"/api/list/{item['id']}", json={"mode": "zelf"}).json()["mode"] == "zelf"
    assert client.post("/api/cart/push").status_code == 200
    assert client.get("/api/meals/suggest?level=4").json()
    assert client.get("/api/baby").json()["allergens"]
    p = client.post("/api/profile", json={"baby_birthdate": "2026-03-01", "organic_level": 1}).json()
    assert p["organic_level"] == 1
    # gratis commandomodus: opdracht uitvoeren, gewoon gepraat in luistermodus negeren
    chat = client.post("/api/chat", json={"speaker": "Kevin", "text": "zet melk en 2 komkommers op de lijst"}).json()
    assert chat["mode"] == "commando" and "melk" in chat["reply"]
    assert client.post("/api/chat", json={"speaker": "Sanne", "text": "hoe was je dag", "respond": False}).json()["reply"] is None
    shown = [m["display"] for m in client.get("/api/chat").json()["messages"]]
    assert shown[:2] == ["zet melk en 2 komkommers op de lijst", "✓ Op de lijst: melk, 2× komkommers."]


def test_pin(app):
    app.settings.pin = "1234"
    client = TestClient(create_app(app))
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"x-pin": "1234"}).status_code == 200


def test_budget_switches_to_free_mode(app):
    from picnicnow import usage

    app.settings.anthropic_api_key = "x"
    app.update_profile(monthly_budget_usd=0.01)
    assert app.assistant_status()["mode"] == "claude"
    usage.record(app.db, "claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 1000})
    st = app.assistant_status()
    assert st["mode"] == "commando" and "budget" in st["reason"]
    assert st["usage"]["cost_usd"] == 0.024
