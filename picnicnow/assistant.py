"""De gespreksassistent: Claude luistert mee met jullie gesprek en beheert menu en lijst via tools."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date
from typing import Any, Callable

from . import baby, combine, meals
from .core import App
from .db import now_iso
from .deals import service as deals
from .history import due_basics
from .meals import LEVELS
from .text import DAY_NAMES, DAYS, euro

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 12
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_TEMPLATE = """Je bent de boodschappen- en kookassistent van een gezin. Je luistert mee met het gesprek \
tussen {names} over de weekboodschappen en het weekmenu, en je helpt actief mee.

Huishouden
- Volwassenen: {names} ({persons} personen eten mee).
- Baby: {baby_name}, {baby_age}. Fase: {baby_stage}.
- Bio-voorkeur: {organic}.
- Dieet/voorkeuren: {diet_notes}
- Keuken/tijd: {kitchen_notes}
- Weekbudget: {budget}

Hoe je werkt
- Antwoord in het Nederlands, kort en praktisch (het is een live gesprek; max ~5 zinnen of een korte lijst).
- Berichten beginnen met de naam van wie praat ("Naam: …"). Spreek mensen aan bij naam als dat helpt.
- Voer wijzigingen direct uit met de tools (lijst, menu) zodra het gesprek daar duidelijk om vraagt; \
vraag alleen door als iets echt onduidelijk is. Bevestig daarna kort wat je hebt gedaan.
- Producten op de lijst: gebruik generieke namen ("kipfilet", "trostomaten"); de app kiest zelf het \
product op basis van eerdere Picnic-bestellingen en de bio-voorkeur.
- Sommige producten willen ze zelf kiezen (modus 'kiezen') of zelf in de winkel halen (modus 'zelf'). \
Respecteer dat en stel het in als ze dat zeggen.
- Gerechtniveaus: 1 = vertrouwd (vaak gemaakt), 2 = bekend, 3 = kant-en-klaar van Picnic, \
4 = nieuw maar doordeweeks haalbaar, 5 = nieuw en uitdagend. Gebruik dit als ze om 'iets makkelijks' \
of 'iets nieuws' vragen. Een gerecht dat niet in de bibliotheek staat mag je toevoegen met ingrediënten \
voor 2 volwassenen.
- Denk mee: combineer gerechten zodat restjes (kruiden, zuivel, groente) opgaan, kook één keer en eet \
twee keer, en laat de baby waar kan 'met de pot mee' eten (portie eruit vóór zout/pittige kruiden).
- Gezond: veel groente, peulvruchten, 1x per week vis, weinig zout. Wees concreet, niet belerend.
- Baby-advies volgt het Voedingscentrum. Wees precies: geen honing < 1 jaar, geen zout toevoegen, \
geen hele noten, ei goed gaar, pinda en ei vóór 8 maanden introduceren. Bij twijfel: consultatiebureau.
- Wijs op aanbiedingen elders alleen als het écht scheelt (gebruik de tool).
- Bestel nooit zelf: je kunt hooguit het Picnic-mandje vullen, en alleen als ze daarom vragen.
- Niet elk bericht vraagt om een reactie van jou: als ze vooral onderling praten, verwerk wat \
duidelijk is (bv. iets op de lijst) en houd je antwoord dan extra kort."""


def _organic_text(level: int) -> str:
    return {0: "geen speciale voorkeur", 1: "bio als het niet veel duurder is",
            2: "bij voorkeur altijd biologisch"}.get(int(level), "bij voorkeur biologisch")


def build_system(app: App) -> str:
    p = app.profile()
    st = baby.stage(app.baby_birthdate)
    months = st.get("months")
    names = p["names"]
    names_text = " en ".join(names) if len(names) <= 2 else ", ".join(names)
    return SYSTEM_TEMPLATE.format(
        names=names_text,
        persons=p["persons"],
        baby_name=p["baby_name"],
        baby_age=f"{months} maanden" if months is not None else "leeftijd onbekend (vraag ernaar)",
        baby_stage=f"{st['label']} – textuur: {st['texture']}",
        organic=_organic_text(p["organic_level"]),
        diet_notes=p.get("diet_notes") or "geen bijzonderheden bekend",
        kitchen_notes=p.get("kitchen_notes") or "doordeweeks liefst ≤ 35 min",
        budget=euro(p["budget_week"] * 100) if p.get("budget_week") else "niet ingesteld",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
DAY_ENUM = DAYS
SLOT_ENUM = ["diner", "lunch", "ontbijt", "baby-ontbijt", "baby-lunch", "baby-diner", "baby-tussendoor"]
MODE_ENUM = ["auto", "kiezen", "zelf"]

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "naam": {"type": "string", "description": "Generieke productnaam, bv. 'trostomaten'"},
        "hoeveelheid": {"type": "number"},
        "eenheid": {"type": "string", "description": "g, ml, stuk, pak, bos, …"},
        "bio": {"type": "boolean", "description": "Expliciet wel/niet biologisch (laat weg = huisvoorkeur)"},
        "modus": {"type": "string", "enum": MODE_ENUM,
                  "description": "auto = app kiest product; kiezen = zij kiezen zelf in de app; zelf = zelf in de winkel halen"},
        "notitie": {"type": "string"},
    },
    "required": ["naam"],
}

INGREDIENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "qty": {"type": "number"}, "unit": {"type": "string"},
        "pantry": {"type": "boolean", "description": "voorraadkast (olie, kruiden, zout) – niet op de lijst"},
    },
    "required": ["name"],
}

TOOLS: list[dict] = [
    {"name": "week_overzicht",
     "description": "Huidige weekmenu, boodschappenlijst (met gekozen producten en prijzen), samenvatting en combineertips.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "zet_op_lijst",
     "description": "Zet één of meer producten op de boodschappenlijst (dubbele worden samengevoegd).",
     "input_schema": {"type": "object", "properties": {
         "items": {"type": "array", "items": ITEM_SCHEMA},
         "wie": {"type": "string", "description": "Naam van wie het vroeg"}}, "required": ["items"]}},
    {"name": "haal_van_lijst", "description": "Haal producten van de lijst.",
     "input_schema": {"type": "object", "properties": {"namen": {"type": "array", "items": {"type": "string"}}},
                      "required": ["namen"]}},
    {"name": "wijzig_item", "description": "Pas hoeveelheid, modus, bio-voorkeur of notitie van een lijst-item aan.",
     "input_schema": {"type": "object", "properties": {
         "naam": {"type": "string"}, "hoeveelheid": {"type": "number"}, "eenheid": {"type": "string"},
         "modus": {"type": "string", "enum": MODE_ENUM}, "bio": {"type": "boolean"}, "notitie": {"type": "string"}},
         "required": ["naam"]}},
    {"name": "plan_gerecht",
     "description": "Zet een gerecht op een dag in het weekmenu en (standaard) de ingrediënten op de lijst. "
                    "Bestaat het gerecht nog niet, geef dan 'nieuw_gerecht' mee.",
     "input_schema": {"type": "object", "properties": {
         "dag": {"type": "string", "enum": DAY_ENUM},
         "gerecht": {"type": "string", "description": "Naam van het gerecht (of vrije tekst zoals 'uit eten')"},
         "moment": {"type": "string", "enum": SLOT_ENUM},
         "porties": {"type": "integer"},
         "ingredienten_op_lijst": {"type": "boolean"},
         "nieuw_gerecht": {"type": "object", "properties": {
             "ingredienten": {"type": "array", "items": INGREDIENT_SCHEMA},
             "moeilijkheid": {"type": "integer", "enum": [4, 5]},
             "bekendheid": {"type": "string", "enum": ["vaak", "soms", "nieuw"]},
             "tags": {"type": "array", "items": {"type": "string"}},
             "bereidingstijd_min": {"type": "integer"},
             "babytip": {"type": "string"},
             "baby_eruit_voor": {"type": "string", "description": "bv. 'zout en sambal'"}},
             "required": ["ingredienten"]}},
         "required": ["dag", "gerecht"]}},
    {"name": "verwijder_uit_menu", "description": "Haal een gerecht uit het weekmenu (ingrediënten blijven op de lijst tenzij opgeruimd).",
     "input_schema": {"type": "object", "properties": {
         "dag": {"type": "string", "enum": DAY_ENUM}, "moment": {"type": "string", "enum": SLOT_ENUM},
         "ingredienten_van_lijst": {"type": "boolean"}}, "required": ["dag"]}},
    {"name": "gerecht_suggesties",
     "description": "Suggesties uit de gerechtenbibliotheek, rekening houdend met restjes, variatie, baby en wat recent gegeten is.",
     "input_schema": {"type": "object", "properties": {
         "niveau": {"type": "integer", "enum": [1, 2, 3, 4, 5]}, "aantal": {"type": "integer"},
         "tags": {"type": "array", "items": {"type": "string"},
                  "description": "bv. vega, vis, snel, soep, oven, stamppot, pasta, wereld, restjes, lunch"},
         "max_minuten": {"type": "integer"}, "zoekterm": {"type": "string"}}}},
    {"name": "kant_en_klaar_opties", "description": "Niveau 3: kant-en-klare maaltijden/maaltijdpakketten (eerder besteld + Picnic-aanbod).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "combineer_tips", "description": "Tips om gerechten deze week te combineren (restjes, dubbel koken, baby-batch, balans).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "markeer_bekendheid", "description": "Leg vast hoe bekend een gerecht is (vaak/soms/nieuw) – bepaalt niveau 1/2.",
     "input_schema": {"type": "object", "properties": {
         "gerecht": {"type": "string"}, "bekendheid": {"type": "string", "enum": ["vaak", "soms", "nieuw"]}},
         "required": ["gerecht", "bekendheid"]}},
    {"name": "baby_advies",
     "description": "Babyfase + hoe de baby met een gerecht kan meeëten, of (zonder gerecht) ideeën voor ontbijt/lunch/tussendoor/diner.",
     "input_schema": {"type": "object", "properties": {"gerecht": {"type": "string"}}}},
    {"name": "allergenen", "description": "Status van allergenen-introductie, of registreer dat de baby iets gegeten heeft.",
     "input_schema": {"type": "object", "properties": {
         "gegeven": {"type": "string", "description": "Naam van allergeen/voedingsmiddel dat is gegeven"},
         "reactie": {"type": "string"}}}},
    {"name": "zoek_product", "description": "Zoek productopties (eerder gekocht + Picnic) met prijs en bio-label.",
     "input_schema": {"type": "object", "properties": {"term": {"type": "string"}, "bio": {"type": "boolean"}},
                      "required": ["term"]}},
    {"name": "vaste_boodschappen",
     "description": "Producten die jullie vast kopen en volgens het bestelritme weer op zijn. Met toevoegen=true direct op de lijst.",
     "input_schema": {"type": "object", "properties": {"toevoegen": {"type": "boolean"}}}},
    {"name": "aanbiedingen",
     "description": "Duurdere, vaak bij Picnic gekochte producten die nu goedkoper zijn bij AH, Vomar of DekaMarkt.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "keuzemodus_instellen",
     "description": "Onthoud voor een productsoort of jullie het zelf willen kiezen ('kiezen'), zelf halen ('zelf') of de app laten kiezen ('auto').",
     "input_schema": {"type": "object", "properties": {
         "trefwoord": {"type": "string", "description": "bv. 'tomaat', 'avocado', 'brood'"},
         "modus": {"type": "string", "enum": MODE_ENUM}}, "required": ["trefwoord", "modus"]}},
    {"name": "naar_mandje",
     "description": "Zet alle 'auto'- en gekozen items in het Picnic-mandje (bestelt niet!). Alleen gebruiken als ze erom vragen.",
     "input_schema": {"type": "object", "properties": {}}},
]


class ToolError(Exception):
    pass


class Tools:
    """Voert tool-aanroepen uit tegen de app. Elke methode geeft JSON-serialiseerbare data terug."""

    def __init__(self, app: App, week: str, speaker: str | None = None):
        self.app = app
        self.week = week
        self.speaker = speaker
        self.changed = False

    def run(self, name: str, args: dict) -> Any:
        fn: Callable | None = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ToolError(f"Onbekende tool {name}")
        return fn(**(args or {}))

    # -- helpers --
    def _item_view(self, i: dict) -> dict:
        return {"naam": i["name"], "hoeveelheid": i["quantity"], "eenheid": i["unit"], "modus": i["mode"],
                "product": i["product_name"], "aantal": i["product_count"],
                "prijs": euro(i["price_cents"]) if i["price_cents"] else None,
                "bio": bool(i["is_organic"]) if i["is_organic"] is not None else None,
                "status": i["status"], "voor": i["source"]}

    # -- tools --
    def t_week_overzicht(self) -> dict:
        st = self.app.state(self.week)
        return {
            "week": self.week,
            "menu": [{"dag": DAY_NAMES[r["day"]], "moment": r["slot"],
                      "gerecht": (r["meal"] or {}).get("name") or r["title"],
                      "niveau": (r["meal"] or {}).get("level"), "gekookt": bool(r["cooked"])} for r in st["plan"]],
            "lijst": [self._item_view(i) for i in st["list"]],
            "samenvatting": {**st["summary"], "geschat": euro(st["summary"]["estimated_cents"])},
            "tips": [f"{t['title']}: {t['detail']}" for t in st["tips"]],
        }

    def t_zet_op_lijst(self, items: list[dict], wie: str | None = None) -> dict:
        added = []
        for it in items:
            if not it.get("naam"):
                continue
            row = self.app.shopping.add(self.week, it["naam"], it.get("hoeveelheid") or 1, it.get("eenheid"),
                                        source="gesprek", added_by=wie or self.speaker, mode=it.get("modus"),
                                        note=it.get("notitie"), organic=it.get("bio"))
            added.append(f"{row['name']} ({row['quantity']:g}{' ' + row['unit'] if row['unit'] else ''}, {row['mode']})")
        self.changed = True
        return {"toegevoegd": added}

    def t_haal_van_lijst(self, namen: list[str]) -> dict:
        removed, missing = [], []
        for n in namen:
            item = self.app.shopping.find(self.week, n)
            if item:
                self.app.shopping.remove(item["id"])
                removed.append(item["name"])
            else:
                missing.append(n)
        self.changed = True
        return {"verwijderd": removed, "niet_gevonden": missing}

    def t_wijzig_item(self, naam: str, hoeveelheid: float | None = None, eenheid: str | None = None,
                      modus: str | None = None, bio: bool | None = None, notitie: str | None = None) -> dict:
        item = self.app.shopping.find(self.week, naam)
        if not item:
            raise ToolError(f"'{naam}' staat niet op de lijst")
        updated = self.app.shopping.update(item["id"], quantity=hoeveelheid, unit=eenheid, mode=modus,
                                           note=notitie, is_organic=None if bio is None else int(bio))
        if bio is not None or hoeveelheid is not None:
            # opnieuw product kiezen met nieuwe voorkeur/hoeveelheid
            self.app.db.execute("UPDATE list_items SET product_id = NULL, product_name = NULL, price_cents = NULL "
                                "WHERE id = ?", (item["id"],))
            self.app.shopping.resolve(self.week)
            updated = self.app.shopping.get(item["id"])
        self.changed = True
        return self._item_view(updated)

    def t_plan_gerecht(self, dag: str, gerecht: str, moment: str = "diner", porties: int | None = None,
                       ingredienten_op_lijst: bool = True, nieuw_gerecht: dict | None = None) -> dict:
        db = self.app.db
        meal = meals.get_meal(db, gerecht)
        if meal is None and nieuw_gerecht:
            meal = meals.upsert_meal(
                db, gerecht, nieuw_gerecht.get("ingredienten", []),
                difficulty=nieuw_gerecht.get("moeilijkheid", 4), familiarity=nieuw_gerecht.get("bekendheid"),
                tags=nieuw_gerecht.get("tags", []), prep_minutes=nieuw_gerecht.get("bereidingstijd_min"),
                baby={"tip": nieuw_gerecht.get("babytip"), "take_out_before": nieuw_gerecht.get("baby_eruit_voor"),
                      "from_months": 6, "servings": 2},
                source="gesprek")
        if meal is None:
            if ingredienten_op_lijst:
                raise ToolError(f"'{gerecht}' staat niet in de bibliotheek. Geef 'nieuw_gerecht' met ingrediënten "
                                "mee, of zet ingredienten_op_lijst=false voor een vrije notitie (bv. 'uit eten').")
            meals.plan_set(db, self.week, dag, moment, title=gerecht, servings=porties)
            self.changed = True
            return {"gepland": f"{DAY_NAMES[dag]} {moment}: {gerecht}"}
        meals.plan_set(db, self.week, dag, moment, meal_id=meal["id"], servings=porties)
        added = []
        if ingredienten_op_lijst:
            added = [i["name"] for i in self.app.shopping.add_meal(self.week, meal, porties, self.speaker)]
        self.changed = True
        result = {"gepland": f"{DAY_NAMES[dag]} {moment}: {meal['name']}", "niveau": meal["level"],
                  "op_lijst": added, "voorraadkast_checken": self.app.shopping.pantry_check(meal)}
        if not moment.startswith("baby"):
            adapt = baby.adapt_meal(meal, self.app.baby_birthdate)
            result["baby"] = {"kan_mee": adapt["verdict"], "hoe": adapt["steps"][:2],
                              "let_op": [w["msg"] for w in adapt["warnings"][:3]]}
        return result

    def t_verwijder_uit_menu(self, dag: str, moment: str = "diner", ingredienten_van_lijst: bool = False) -> dict:
        row = next((r for r in meals.plan_get(self.app.db, self.week) if r["day"] == dag and r["slot"] == moment), None)
        if not row:
            raise ToolError("Daar staat niets gepland")
        removed = []
        if ingredienten_van_lijst and row["meal"]:
            name = row["meal"]["name"]
            for item in self.app.shopping.items(self.week):
                sources = [s for s in (item["source"] or "").split(", ") if s]
                if sources == [name] and item["status"] == "open":
                    self.app.shopping.remove(item["id"])
                    removed.append(item["name"])
        meals.plan_clear(self.app.db, self.week, dag, moment)
        self.changed = True
        return {"verwijderd": f"{DAY_NAMES[dag]} {moment}", "van_lijst": removed}

    def t_gerecht_suggesties(self, niveau: int | None = None, aantal: int = 5, tags: list[str] | None = None,
                             max_minuten: int | None = None, zoekterm: str | None = None) -> dict:
        if niveau == 3:
            return self.t_kant_en_klaar_opties()
        sug = meals.suggest(self.app.db, self.week, level=niveau, count=aantal or 5, tags=tags,
                            max_minutes=max_minuten, baby_months=self.app.baby_months, query=zoekterm)
        result = {"suggesties": [{"gerecht": s["name"], "niveau": f"{s['level']} {s['level_label']}",
                                  "minuten": s["prep_minutes"], "waarom": s["reasons"]} for s in sug]}
        if niveau in (1, 2) and not sug:
            result["hint"] = ("Nog geen vertrouwde gerechten bekend. Vraag welke gerechten ze vaak maken en leg "
                              "dat vast met markeer_bekendheid. Waarschijnlijk bekend o.b.v. bestellingen: "
                              + ", ".join(x["meal"] for x in meals.likely_known(self.app.db)[:5]))
        return result

    def t_kant_en_klaar_opties(self) -> dict:
        opts = meals.ready_meals(self.app.db, self.app.picnic)
        return {"kant_en_klaar": [{"gerecht": o["name"], "prijs": euro(o["price_cents"]), "bio": o["is_organic"],
                                   "eerder_gekocht": o["times_bought"], "bron": o["source"]} for o in opts[:10]]}

    def t_combineer_tips(self) -> dict:
        return {"tips": [f"{t['title']}: {t['detail']}"
                         for t in combine.combine_week(self.app.db, self.week, self.app.baby_months)]}

    def t_markeer_bekendheid(self, gerecht: str, bekendheid: str) -> dict:
        m = meals.set_familiarity(self.app.db, gerecht, bekendheid)
        if m is None:
            m = meals.upsert_meal(self.app.db, gerecht, [], familiarity=bekendheid, source="gesprek")
            return {"nieuw_gerecht": m["name"], "niveau": m["level"],
                    "hint": "Ingrediënten onbekend; vraag ze of plan het met nieuw_gerecht als het op het menu komt."}
        self.changed = True
        return {"gerecht": m["name"], "niveau": f"{m['level']} {m['level_label']}"}

    def t_baby_advies(self, gerecht: str | None = None) -> dict:
        if gerecht:
            m = meals.get_meal(self.app.db, gerecht)
            if not m:
                warnings = baby.check_ingredients([gerecht], self.app.baby_months)
                return {"fase": baby.stage(self.app.baby_birthdate),
                        "let_op": [w["msg"] for w in warnings],
                        "hint": "Gerecht onbekend in de bibliotheek; geef algemeen advies voor deze fase."}
            a = baby.adapt_meal(m, self.app.baby_birthdate)
            return {"gerecht": m["name"], "fase": a["stage"]["label"], "kan_mee": a["verdict"], "hoe": a["steps"],
                    "let_op": [w["msg"] for w in a["warnings"]]}
        info = baby.ideas(self.app.baby_birthdate)
        return {"fase": info["stage"], "ideeen": info["ideas"],
                "bron": baby.guide()["source_note"]}

    def t_allergenen(self, gegeven: str | None = None, reactie: str | None = None) -> dict:
        if gegeven:
            baby.log_food(self.app.db, gegeven.strip().lower(), reactie)
            self.changed = True
        status = baby.allergen_status(self.app.db, self.app.baby_birthdate)
        return {"allergenen": [{"naam": a["name"], "status": a["status"], "keer": a["times_given"],
                                "advies": a["advice"], "nu_aandacht": a["urgent"]} for a in status]}

    def t_zoek_product(self, term: str, bio: bool | None = None) -> dict:
        cands = self.app.shopping.candidates(term, bio)
        return {"opties": [{"product": c.name, "prijs": euro(c.price_cents), "eenheid": c.unit_quantity,
                            "bio": c.is_organic, "eerder_gekocht": c.times_bought, "id": c.id}
                           for c in cands[:6]]}

    def t_vaste_boodschappen(self, toevoegen: bool = False) -> dict:
        if toevoegen:
            added = self.app.shopping.add_basics(self.week, self.speaker)
            self.changed = True
            return {"toegevoegd": [a["name"] for a in added]}
        return {"weer_op": [{"product": s.name, "laatst": s.last_bought, "elke_dagen": s.avg_interval_days,
                             "gem_aantal": s.avg_quantity} for s in due_basics(self.app.db)]}

    def t_aanbiedingen(self) -> dict:
        opps = deals.opportunities(self.app.db, self.app.settings, organic_level=self.app.profile()["organic_level"])
        refreshed = self.app.db.get_pref("deals_refreshed")
        return {"bijgewerkt": refreshed or "nog nooit – laat ze op 'Aanbiedingen verversen' tikken",
                "kansen": [{"product": o["product"]["name"], "winkel": o["store_name"], "type": o["type"],
                            "aanbieding": o["deal"]["title"], "actie": o["deal"].get("label"),
                            "besparing_per_stuk": euro(o["saving_cents"]),
                            "niet_bio": o.get("organic_mismatch", False)} for o in opps[:8]]}

    def t_keuzemodus_instellen(self, trefwoord: str, modus: str) -> dict:
        rules = self.app.shopping.set_mode_rule(trefwoord, modus)
        for item in self.app.shopping.items(self.week):
            if item["status"] == "open":
                self.app.shopping.update(item["id"], mode=self.app.shopping.mode_for(item["name"]))
        self.changed = True
        return {"regels": rules}

    def t_naar_mandje(self) -> dict:
        res = self.app.shopping.push_to_cart(self.week)
        self.changed = True
        cart = res.get("cart") or {}
        return {"in_mandje": res["added"], "overgeslagen": res["skipped"], "fouten": res["errors"],
                "mandje_totaal": euro(cart.get("total_cents"))}


# ---------------------------------------------------------------------------
# Gesprek
# ---------------------------------------------------------------------------
def _block_dicts(content) -> list[dict]:
    return [b.to_dict(mode="json") if hasattr(b, "to_dict") else b for b in content]


def _display_text(blocks: list[dict]) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def repair_tool_pairs(messages: list[dict]) -> list[dict]:
    """Een tool_use moet direct gevolgd worden door de tool_results. Na een crash halverwege kan dat
    ontbreken; vul dan een foutresultaat in zodat het gesprek bruikbaar blijft."""
    out: list[dict] = []
    for i, msg in enumerate(messages):
        out.append(msg)
        if msg["role"] != "assistant":
            continue
        ids = [b["id"] for b in msg["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not ids:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        answered = {b.get("tool_use_id") for b in (nxt or {}).get("content", []) if isinstance(b, dict)}
        if nxt is None or nxt["role"] != "user" or not set(ids) <= answered:
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": t, "content": "Onderbroken.", "is_error": True} for t in ids]})
    return out


class Assistant:
    def __init__(self, app: App, client=None):
        self.app = app
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    # -- gesprekken --
    def conversation_id(self, week: str, new: bool = False) -> int:
        row = None if new else self.app.db.one(
            "SELECT id FROM conversations WHERE week = ? ORDER BY id DESC LIMIT 1", (week,))
        if row:
            return row["id"]
        return self.app.db.execute("INSERT INTO conversations(week, created_at) VALUES (?, ?)", (week, now_iso()))

    def history(self, conversation_id: int) -> list[dict]:
        return self.app.db.query(
            "SELECT id, role, speaker, display, created_at FROM messages WHERE conversation_id = ? "
            "AND display IS NOT NULL AND display != '' ORDER BY id", (conversation_id,))

    def _store(self, conversation_id: int, role: str, content: list[dict], speaker: str | None,
               display: str | None) -> int:
        return self.app.db.execute(
            "INSERT INTO messages(conversation_id, role, speaker, content, display, created_at) VALUES (?,?,?,?,?,?)",
            (conversation_id, role, speaker, json.dumps(content, ensure_ascii=False), display, now_iso()))

    def _api_messages(self, conversation_id: int) -> list[dict]:
        rows = self.app.db.query("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                                 (conversation_id,))
        return repair_tool_pairs([{"role": r["role"], "content": json.loads(r["content"])} for r in rows])

    def note(self, week: str, speaker: str, text: str) -> int:
        """Bewaar een gespreksfragment zonder (nog) te reageren (luistermodus).
        Wacht op een lopend antwoord, zodat tool_use en tool_result aaneengesloten blijven."""
        with self._lock:
            cid = self.conversation_id(week)
            self._store(cid, "user", [{"type": "text", "text": f"{speaker}: {text}"}], speaker, text)
            return cid

    def respond(self, week: str, speaker: str | None, text: str | None, on_change: Callable[[], None] | None = None) -> dict:
        """Verwerk een bericht en geef het antwoord van de assistent."""
        with self._lock:
            cid = self.conversation_id(week)
            content = []
            if text:
                content.append({"type": "text", "text": f"{speaker or 'Iemand'}: {text}"})
            else:
                content.append({"type": "text", "text": "(Jullie praatten onderling; verwerk wat er gezegd is.)"})
            content.append({"type": "text", "text": self.app.compact_state(week)})
            self._store(cid, "user", content, speaker, text or None)
            tools = Tools(self.app, week, speaker)
            reply_parts: list[str] = []
            try:
                for _ in range(MAX_TOOL_ROUNDS):
                    response = self._call(cid)
                    blocks = _block_dicts(response.content)
                    self._store(cid, "assistant", blocks, "assistent", _display_text(blocks) or None)
                    text_out = _display_text(blocks)
                    if text_out:
                        reply_parts.append(text_out)
                    if response.stop_reason == "refusal":
                        reply_parts.append("Daar kan ik helaas niet mee helpen.")
                        break
                    if response.stop_reason != "tool_use":
                        break
                    results = []
                    for b in blocks:
                        if b.get("type") != "tool_use":
                            continue
                        try:
                            out = tools.run(b["name"], b.get("input") or {})
                            results.append({"type": "tool_result", "tool_use_id": b["id"],
                                            "content": json.dumps(out, ensure_ascii=False, default=str)})
                        except Exception as exc:  # fout terug naar het model, dan kan het herstellen
                            log.info("Tool %s faalde: %s", b["name"], exc)
                            results.append({"type": "tool_result", "tool_use_id": b["id"],
                                            "content": f"Fout: {exc}", "is_error": True})
                    self._store(cid, "user", results, None, None)
                    if tools.changed and on_change:
                        on_change()
                else:
                    reply_parts.append("(Ik ben even gestopt na veel stappen – vraag gerust verder.)")
            except Exception as exc:
                log.exception("Assistent-fout")
                return {"reply": f"Er ging iets mis met de assistent: {exc}", "changed": tools.changed, "error": True}
            return {"reply": "\n\n".join(reply_parts).strip(), "changed": tools.changed}

    def _call(self, conversation_id: int):
        s = self.app.settings
        return self.client.beta.messages.create(
            model=s.model,
            max_tokens=16000,
            system=[{"type": "text", "text": build_system(self.app), "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=self._api_messages(conversation_id),
            output_config={"effort": s.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )


def today_label() -> str:
    d = date.today()
    return f"{DAY_NAMES[DAYS[d.weekday()]]} {d.isoformat()}"


__all__ = ["Assistant", "Tools", "TOOLS", "LEVELS", "build_system", "today_label"]
