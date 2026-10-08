"""Webserver: REST-API + websocket voor een gedeelde, live sessie op meerdere telefoons."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import baby, meals
from .assistant import Assistant
from .core import App
from .deals import service as deals
from .history import staples, sync_history
from .picnic import PicnicUnavailable, TwoFactorRequired

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"


class Hub:
    """Houdt verbonden apparaten bij en stuurt iedereen updates."""

    def __init__(self):
        self.clients: dict[WebSocket, str] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    async def join(self, ws: WebSocket, name: str):
        await ws.accept()
        self.loop = asyncio.get_running_loop()
        self.clients[ws] = name
        await self.broadcast({"type": "presence", "online": sorted(set(self.clients.values()))})

    async def leave(self, ws: WebSocket):
        self.clients.pop(ws, None)
        await self.broadcast({"type": "presence", "online": sorted(set(self.clients.values()))})

    async def broadcast(self, event: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    def broadcast_threadsafe(self, event: dict):
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), self.loop)


# --- request-modellen ----------------------------------------------------------
class ChatIn(BaseModel):
    speaker: str | None = None
    text: str | None = None
    respond: bool = True


class ItemIn(BaseModel):
    name: str
    quantity: float | None = 1
    unit: str | None = None
    mode: str | None = None
    organic: bool | None = None
    note: str | None = None
    added_by: str | None = None


class ItemPatch(BaseModel):
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    mode: str | None = None
    status: str | None = None
    note: str | None = None
    product_count: int | None = None
    organic: bool | None = None


class ChooseIn(BaseModel):
    product_id: str


class PlanIn(BaseModel):
    day: str
    slot: str = "diner"
    meal_id: int | None = None
    title: str | None = None
    servings: int | None = None
    add_to_list: bool = True


class MealIn(BaseModel):
    name: str
    ingredients: list[dict] = []
    difficulty: int = 4
    familiarity: str = "nieuw"
    tags: list[str] = []
    prep_minutes: int | None = None
    baby_tip: str | None = None


class FamiliarityIn(BaseModel):
    familiarity: str


class BabyLogIn(BaseModel):
    name: str
    reaction: str | None = None


class ModeRuleIn(BaseModel):
    keyword: str
    mode: str


class WeekIn(BaseModel):
    week: str


class TwoFAIn(BaseModel):
    code: str | None = None
    channel: str = "SMS"


def create_app(app: App | None = None, assistant: Assistant | None = None) -> FastAPI:
    core = app or App()
    bot = assistant or Assistant(core)
    hub = Hub()
    api = FastAPI(title="PicNicNow", version="0.1.0")

    # --- optionele pincode -------------------------------------------------------
    def check_pin(request: Request):
        pin = core.settings.pin
        if pin and request.headers.get("x-pin") != pin and request.cookies.get("pin") != pin:
            raise HTTPException(401, "Pincode vereist")

    guard = [Depends(check_pin)]

    def changed(week: str | None = None):
        hub.broadcast_threadsafe({"type": "state", "week": week or core.active_week()})

    async def notify(week: str | None = None):
        await hub.broadcast({"type": "state", "week": week or core.active_week()})

    @api.exception_handler(PicnicUnavailable)
    async def _picnic_down(_, exc):
        return JSONResponse({"detail": f"Picnic: {exc}"}, status_code=503)

    @api.exception_handler(TwoFactorRequired)
    async def _twofa(_, exc):
        return JSONResponse({"detail": "Picnic vraagt om een verificatiecode (2FA).", "twofa": True}, status_code=409)

    # --- pagina's ----------------------------------------------------------------
    @api.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.exists():
        api.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @api.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    # --- stand ---------------------------------------------------------------------
    @api.get("/api/state", dependencies=guard)
    def state(week: str | None = None):
        return core.state(week)

    @api.post("/api/week", dependencies=guard)
    async def set_week(body: WeekIn):
        core.set_week(body.week)
        await notify(body.week)
        return core.state(body.week)

    @api.get("/api/profile", dependencies=guard)
    def get_profile():
        return core.profile()

    @api.post("/api/profile", dependencies=guard)
    async def set_profile(body: dict):
        p = core.update_profile(**body)
        await notify()
        return p

    # --- gesprek ---------------------------------------------------------------------
    @api.get("/api/chat", dependencies=guard)
    def chat_history(week: str | None = None):
        week = week or core.active_week()
        cid = bot.conversation_id(week)
        return {"conversation_id": cid, "messages": bot.history(cid), "enabled": core.settings.assistant_enabled}

    @api.post("/api/chat/new", dependencies=guard)
    async def chat_new():
        cid = bot.conversation_id(core.active_week(), new=True)
        await hub.broadcast({"type": "chat_reset"})
        return {"conversation_id": cid}

    @api.post("/api/chat", dependencies=guard)
    async def chat(body: ChatIn):
        week = core.active_week()
        if body.text:
            await hub.broadcast({"type": "message", "role": "user", "speaker": body.speaker, "display": body.text})
        if not body.respond:
            if body.text:
                await run_in_threadpool(bot.note, week, body.speaker or "Wij", body.text)
            return {"reply": None}
        if not core.settings.assistant_enabled:
            if body.text:
                await run_in_threadpool(bot.note, week, body.speaker or "Wij", body.text)
            msg = "De assistent staat uit: zet ANTHROPIC_API_KEY in .env om het gesprek te activeren."
            await hub.broadcast({"type": "message", "role": "assistant", "speaker": "assistent", "display": msg})
            return {"reply": msg, "changed": False}
        await hub.broadcast({"type": "typing", "on": True})
        try:
            result = await run_in_threadpool(bot.respond, week, body.speaker, body.text, lambda: changed(week))
        finally:
            await hub.broadcast({"type": "typing", "on": False})
        if result.get("reply"):
            await hub.broadcast({"type": "message", "role": "assistant", "speaker": "assistent",
                                 "display": result["reply"]})
        if result.get("changed"):
            await notify(week)
        return result

    # --- boodschappenlijst ----------------------------------------------------------------
    @api.post("/api/list", dependencies=guard)
    async def add_item(body: ItemIn):
        week = core.active_week()
        item = core.shopping.add(week, body.name, body.quantity, body.unit, source="handmatig",
                                 added_by=body.added_by, mode=body.mode, note=body.note, organic=body.organic)
        await notify(week)
        return item

    @api.patch("/api/list/{item_id}", dependencies=guard)
    async def patch_item(item_id: int, body: ItemPatch):
        fields = body.model_dump(exclude_none=True)
        organic = fields.pop("organic", None)
        if organic is not None:
            fields["is_organic"] = int(organic)
        item = core.shopping.update(item_id, **fields)
        if organic is not None or "quantity" in fields:
            if item and item["product_id"] and organic is not None:
                core.db.execute("UPDATE list_items SET product_id = NULL, product_name = NULL, price_cents = NULL "
                                "WHERE id = ?", (item_id,))
            await run_in_threadpool(core.shopping.resolve, core.active_week())
            item = core.shopping.get(item_id)
        await notify()
        return item

    @api.delete("/api/list/{item_id}", dependencies=guard)
    async def delete_item(item_id: int):
        core.shopping.remove(item_id)
        await notify()
        return {"ok": True}

    @api.get("/api/list/{item_id}/options", dependencies=guard)
    def item_options(item_id: int):
        return core.shopping.options(item_id)

    @api.post("/api/list/{item_id}/choose", dependencies=guard)
    async def choose(item_id: int, body: ChooseIn):
        item = await run_in_threadpool(core.shopping.choose, item_id, body.product_id)
        await notify()
        return item

    @api.post("/api/list/basics", dependencies=guard)
    async def basics():
        added = core.shopping.add_basics(core.active_week())
        await notify()
        return added

    @api.post("/api/list/resolve", dependencies=guard)
    async def resolve():
        res = await run_in_threadpool(core.shopping.resolve, core.active_week())
        await notify()
        return res

    @api.post("/api/cart/push", dependencies=guard)
    async def push_cart():
        res = await run_in_threadpool(core.shopping.push_to_cart, core.active_week())
        await notify()
        return res

    @api.get("/api/cart", dependencies=guard)
    def cart():
        return core.picnic.get_cart().to_dict()

    @api.post("/api/mode-rules", dependencies=guard)
    async def mode_rule(body: ModeRuleIn):
        rules = core.shopping.set_mode_rule(body.keyword, body.mode)
        for item in core.shopping.items(core.active_week()):
            if item["status"] == "open":
                core.shopping.update(item["id"], mode=core.shopping.mode_for(item["name"]))
        await notify()
        return rules

    # --- weekmenu ----------------------------------------------------------------
    @api.post("/api/plan", dependencies=guard)
    async def plan(body: PlanIn):
        week = core.active_week()
        if body.meal_id:
            meal = meals.get_meal(core.db, body.meal_id)
            if not meal:
                raise HTTPException(404, "Gerecht niet gevonden")
            meals.plan_set(core.db, week, body.day, body.slot, meal_id=meal["id"], servings=body.servings)
            if body.add_to_list:
                core.shopping.add_meal(week, meal, body.servings)
        else:
            meals.plan_set(core.db, week, body.day, body.slot, title=body.title, servings=body.servings)
        await notify(week)
        return core.state(week)["plan"]

    @api.delete("/api/plan/{day}/{slot}", dependencies=guard)
    async def unplan(day: str, slot: str):
        meals.plan_clear(core.db, core.active_week(), day, slot)
        await notify()
        return {"ok": True}

    @api.post("/api/plan/{day}/{slot}/cooked", dependencies=guard)
    async def cooked(day: str, slot: str):
        m = meals.mark_cooked(core.db, core.active_week(), day, slot)
        await notify()
        return m or {"ok": True}

    # --- gerechten -----------------------------------------------------------------
    @api.get("/api/meals", dependencies=guard)
    def list_meals(level: int | None = None, tag: str | None = None):
        return meals.list_meals(core.db, level, tag)

    @api.get("/api/meals/suggest", dependencies=guard)
    def suggest(level: int | None = None, count: int = 6, tag: str | None = None, q: str | None = None):
        return meals.suggest(core.db, core.active_week(), level=level, count=count, tags=[tag] if tag else None,
                             baby_months=core.baby_months, query=q)

    @api.get("/api/meals/ready", dependencies=guard)
    def ready():
        return meals.ready_meals(core.db, core.picnic)

    @api.get("/api/meals/likely-known", dependencies=guard)
    def likely_known():
        return meals.likely_known(core.db)

    @api.post("/api/meals", dependencies=guard)
    def create_meal(body: MealIn):
        return meals.upsert_meal(core.db, body.name, body.ingredients, difficulty=body.difficulty,
                                 familiarity=body.familiarity, tags=body.tags, prep_minutes=body.prep_minutes,
                                 baby={"tip": body.baby_tip, "from_months": 6} if body.baby_tip else None,
                                 source="handmatig")

    @api.post("/api/meals/{meal_id}/familiarity", dependencies=guard)
    async def familiarity(meal_id: int, body: FamiliarityIn):
        m = meals.set_familiarity(core.db, meal_id, body.familiarity)
        if not m:
            raise HTTPException(400, "Onbekend gerecht of bekendheid")
        await notify()
        return m

    @api.get("/api/meals/{meal_id}", dependencies=guard)
    def meal_detail(meal_id: int):
        m = meals.get_meal(core.db, meal_id)
        if not m:
            raise HTTPException(404)
        return {**m, "baby_adapt": baby.adapt_meal(m, core.baby_birthdate)}

    # --- baby ----------------------------------------------------------------------
    @api.get("/api/baby", dependencies=guard)
    def baby_info():
        return {**baby.ideas(core.baby_birthdate),
                "allergens": baby.allergen_status(core.db, core.baby_birthdate),
                "tried": baby.foods_tried(core.db), "source": baby.guide()["source_note"]}

    @api.post("/api/baby/log", dependencies=guard)
    async def baby_log(body: BabyLogIn):
        row = baby.log_food(core.db, body.name.strip().lower(), body.reaction)
        await notify()
        return row

    # --- aanbiedingen ----------------------------------------------------------------
    @api.get("/api/deals", dependencies=guard)
    def get_deals():
        cj = deals.default_checkjebon(core.settings)
        return {"refreshed": core.db.get_pref("deals_refreshed"),
                "watchlist": deals.watchlist(core.db, core.settings),
                "opportunities": deals.opportunities(core.db, core.settings, cj,
                                                     organic_level=core.profile()["organic_level"])}

    @api.post("/api/deals/refresh", dependencies=guard)
    async def refresh_deals():
        res = await run_in_threadpool(deals.refresh_deals, core.db, core.settings)
        return res

    # --- Picnic ----------------------------------------------------------------------
    @api.get("/api/picnic/status", dependencies=guard)
    def picnic_status():
        return {"demo": core.picnic.demo, "ready": core.picnic.is_ready(),
                "needs_2fa": getattr(core.picnic, "needs_2fa", False), "last_sync": core.db.get_pref("last_sync"),
                "products": core.db.one("SELECT COUNT(*) AS n FROM products")["n"]}

    @api.post("/api/picnic/login", dependencies=guard)
    def picnic_login():
        if core.picnic.demo:
            return {"ok": True, "demo": True}
        core.picnic.login()
        return {"ok": True}

    @api.post("/api/picnic/2fa/request", dependencies=guard)
    def twofa_request(body: TwoFAIn):
        core.picnic.request_2fa(body.channel)
        return {"ok": True}

    @api.post("/api/picnic/2fa/verify", dependencies=guard)
    def twofa_verify(body: TwoFAIn):
        core.picnic.verify_2fa(body.code or "")
        return {"ok": True}

    @api.post("/api/picnic/sync", dependencies=guard)
    async def picnic_sync():
        res = await run_in_threadpool(sync_history, core.db, core.picnic)
        await notify()
        return res

    @api.get("/api/staples", dependencies=guard)
    def get_staples():
        return [s.to_dict() for s in staples(core.db)]

    # --- live --------------------------------------------------------------------------
    @api.websocket("/ws")
    async def ws(websocket: WebSocket, name: str = "Iemand", pin: str | None = None):
        if core.settings.pin and pin != core.settings.pin:
            await websocket.close(code=4401)
            return
        await hub.join(websocket, name)
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "transcript":  # live ondertitels delen met de ander
                    await hub.broadcast({"type": "transcript", "speaker": data.get("speaker"),
                                         "text": data.get("text", "")[:300]})
        except WebSocketDisconnect:
            await hub.leave(websocket)
        except Exception:
            await hub.leave(websocket)

    api.state.core = core
    api.state.hub = hub
    return api
