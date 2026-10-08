"""Gratis commandomodus: begrijpt de meest gebruikte zinnen zonder AI (en zonder API-kosten).

Werkt met getypte én gesproken zinnen. Alleen duidelijke opdrachten worden uitgevoerd; gewoon
gepraat in luistermodus wordt genegeerd. Voorbeelden:

  zet melk, 2 komkommers en een pak biologische yoghurt op de lijst
  de eieren zijn op  ·  we hebben nog kipfilet nodig  ·  avocado zelf halen
  haal de melk van de lijst
  woensdag shakshuka  ·  vrijdag eten we pizza  ·  pasta pesto op dinsdag
  suggesties  ·  iets nieuws?  ·  iets vertrouwds voor maandag  ·  kant-en-klaar ideeën
  vaste boodschappen  ·  aanbiedingen  ·  combineertips  ·  wat kan de baby eten
  wat staat er op de lijst  ·  zet alles in het mandje
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import baby, combine, meals
from .core import App
from .deals import service as deals
from .text import DAY_NAMES, DAYS, euro

DAY_WORDS = {"maandag": "ma", "dinsdag": "di", "woensdag": "wo", "donderdag": "do", "vrijdag": "vr",
             "zaterdag": "za", "zondag": "zo"}
NUMBERS = {"een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
           "negen": 9, "tien": 10, "half": 0.5, "halve": 0.5, "anderhalve": 1.5, "paar": 2}
UNITS = {"gram": "g", "g": "g", "gr": "g", "kilo": "kg", "kg": "kg", "liter": "l", "l": "l", "ml": "ml",
         "pak": "pak", "pakken": "pak", "pakje": "pak", "zak": "zak", "zakken": "zak", "zakje": "zak",
         "bak": "bak", "bakje": "bak", "bakjes": "bak", "bos": "bos", "bosje": "bos", "bosjes": "bos",
         "blik": "blik", "blikken": "blik", "blikje": "blik", "fles": "fles", "flessen": "fles",
         "stuk": "stuk", "stuks": "stuk", "krop": "krop", "netje": "net", "pot": "pot", "potje": "pot"}
LEVEL_WORDS = [("kant en klaar", 3), ("kant-en-klaar", 3), ("maaltijdpakket", 3), ("kantenklaar", 3),
               ("vertrouwd", 1), ("makkelijk", 1), ("simpel", 1), ("bekend", 2), ("uitdag", 5), ("weekend", 5),
               ("ingewikkeld", 5), ("nieuw", 4), ("anders", 4)]
TAG_WORDS = {"vega": "vega", "vegetarisch": "vega", "vis": "vis", "snel": "snel", "soep": "soep", "pasta": "pasta",
             "oven": "oven", "mediterraan": "mediterraan", "avg": "avg", "stamppot": "stamppot"}

DAY_RE = r"(?P<day>maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|vanavond|morgen)(?:avond|middag)?"
LIST_RE = r"(?:de\s+)?(?:boodschappen)?(?:lijst|lijstje)"

ADD_PATTERNS = [
    rf"^(?:zet|doe|schrijf|gooi|plaats)\s+(?P<items>.+?)\s+(?:op|bij|aan|in)\s+{LIST_RE}$",
    r"^(?:we\s+|wij\s+)?(?:hebben|moeten)\s+(?:nog\s+|ook\s+|wel\s+)*(?P<items>.+?)\s+nodig$",
    r"^(?:we\s+moeten\s+)(?:nog\s+|ook\s+)*(?P<items>.+?)\s+(?:halen|kopen|bestellen)$",
    r"^(?:de\s+|het\s+|onze\s+)?(?P<items>.+?)\s+(?:is|zijn|raakt|raken)\s+(?:bijna\s+)?op$",
    r"^(?:boodschappen|lijst)\s*:\s*(?P<items>.+)$",
    r"^(?:en\s+)?(?:ook\s+|nog\s+)*(?P<items>.+?)\s+erbij$",
    r"^(?:en\s+)?(?:ook\s+|nog\s+)*(?P<items>.+?)\s+zelf\s+(?P<mode>halen|kiezen)$",
]
REMOVE_PATTERNS = [
    rf"^(?:haal|streep|schrap|gooi|verwijder)\s+(?P<items>.+?)\s+(?:van|weg\s+van|uit|af)\s+{LIST_RE}$",
    r"^(?:haal|streep|schrap|verwijder)\s+(?P<items>.+?)\s+(?:weg|door|af)$",
    r"^(?:we\s+hebben\s+)?(?:toch\s+)?geen\s+(?P<items>.+?)\s+(?:meer\s+)?nodig$",
]
PLAN_PATTERNS = [
    rf"^(?:op\s+)?{DAY_RE}\s+(?:eten\s+we|maken\s+we|doen\s+we|koken\s+we|wordt\s+het|hebben\s+we)?\s*(?P<dish>.+)$",
    rf"^(?:we\s+)?(?:eten|maken|koken|doen)\s+(?:we\s+)?(?P<dish>.+?)\s+op\s+{DAY_RE}$",
    rf"^(?P<dish>.+?)\s+op\s+{DAY_RE}$",
]

HELP = ("Zonder AI begrijp ik korte opdrachten, bijvoorbeeld:\n"
        "• zet melk en 2 komkommers op de lijst · de eieren zijn op · avocado zelf halen\n"
        "• haal de melk van de lijst\n"
        "• woensdag shakshuka · pasta pesto op dinsdag\n"
        "• suggesties · iets nieuws? · iets vertrouwds voor maandag · kant-en-klaar ideeën\n"
        "• vaste boodschappen · aanbiedingen · combineertips · wat kan de baby eten\n"
        "• wat staat er op de lijst · zet alles in het mandje")


@dataclass
class Item:
    name: str
    quantity: float = 1
    unit: str | None = None
    organic: bool | None = None
    mode: str | None = None


def _clean(text: str) -> str:
    t = text.lower().replace("’", "'").strip()
    t = re.sub(r"[!?¿¡\"“”]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = re.sub(r"^(?:oké?|ok|ja|nou|hé|he|hey|assistent|picnic)[, ]+", "", t)
    return t.strip()


def parse_items(text: str) -> list[Item]:
    parts = re.split(r",|\s+en\s+|\s+plus\s+|\s+&\s+|\s+met daarnaast\s+", text)
    items = []
    for raw in parts:
        p = raw.strip(" .")
        p = re.sub(r"^(?:de|het|nog|ook|wat|wel|even|graag)\s+", "", p)
        p = re.sub(r"^(?:de|het|nog|ook|wat|wel|even|graag)\s+", "", p)
        if not p:
            continue
        item = Item(name=p)
        m = re.match(r"^(\d+(?:[.,]\d+)?|" + "|".join(NUMBERS) + r")\s+(.*)$", p)
        if m:
            q = m.group(1)
            item.quantity = float(q.replace(",", ".")) if q[0].isdigit() else NUMBERS[q]
            p = m.group(2)
        m = re.match(r"^(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")\s+(?:(?:met|van)\s+)?(.*)$", p)
        if m and m.group(2):
            item.unit = UNITS[m.group(1)]
            p = m.group(2)
        m = re.search(r"\s+zelf\s*(halen|kiezen)?$", p)
        if m:
            item.mode = "kiezen" if m.group(1) == "kiezen" else "zelf"
            p = p[: m.start()]
        if re.match(r"^(?:biologische|biologisch|bio)\s+", p):
            item.organic = True
            p = re.sub(r"^(?:biologische|biologisch|bio)\s+", "", p)
        item.name = p.strip()
        if item.name and len(item.name) <= 40 and len(item.name.split()) <= 5:
            items.append(item)
    return items


def _day(word: str) -> str:
    if word in DAY_WORDS:
        return DAY_WORDS[word]
    d = date.today() + (timedelta(days=1) if word == "morgen" else timedelta())
    return DAYS[d.weekday()]


def _level(text: str) -> int | None:
    m = re.search(r"(?:niveau|level)\s*(\d)", text)
    if m:
        return int(m.group(1))
    for word, lvl in LEVEL_WORDS:
        if word in text:
            return lvl
    return None


def _tags(text: str) -> list[str]:
    return sorted({tag for word, tag in TAG_WORDS.items() if re.search(rf"\b{word}\b", text)})


@dataclass
class Reply:
    text: str
    changed: bool = False


class Commands:
    def __init__(self, app: App):
        self.app = app

    def handle(self, week: str, speaker: str | None, text: str) -> Reply | None:
        """Voer alle herkende opdrachten in `text` uit. None = niets herkend."""
        replies, changed = [], False
        for sentence in re.split(r"(?<=[.!?])\s+|\s+en\s+(?=(?:zet|haal|maandag|dinsdag|woensdag|donderdag|"
                                 r"vrijdag|zaterdag|zondag)\b)", text):
            r = self._one(week, speaker, _clean(sentence))
            if r:
                replies.append(r.text)
                changed = changed or r.changed
        return Reply("\n".join(replies), changed) if replies else None

    # ------------------------------------------------------------------
    def _one(self, week: str, speaker: str | None, t: str) -> Reply | None:
        if not t:
            return None
        if re.search(r"^(help|hulp|wat kan je|wat kun je)", t):
            return Reply(HELP)
        if re.search(r"\b(zet|doe)\b.*\b(alles|lijst|boodschappen)\b.*\b(mandje|winkelmand|winkelwagen)\b", t):
            return self._cart(week)
        if re.search(r"wat staat er op (de |het )?(boodschappen)?lijst", t):
            return self._show_list(week)
        if "vaste boodschappen" in t:
            added = self.app.shopping.add_basics(week, speaker)
            return Reply("Vaste boodschappen toegevoegd: " + ", ".join(a["name"] for a in added) if added
                         else "Volgens jullie ritme is er nu niets op.", bool(added))
        if re.search(r"aanbieding|goedkoper|korting", t):
            return self._deals()
        if re.search(r"combineer|combineren|restje", t):
            tips = combine.combine_week(self.app.db, week, self.app.baby_months, self.app.baby_profile)
            return Reply("\n".join(f"• {x['title']}: {x['detail']}" for x in tips[:4]) or
                         "Plan eerst een paar gerechten, dan geef ik combineertips.")
        m = re.search(r"kan (?:de )?baby (?P<dish>.+?) (?:mee[- ]?)?eten", t)
        if m and m.group("dish") not in ("ook", "wat", "dit", "dat"):
            return self._baby_meal(m.group("dish"))
        if "baby" in t and re.search(r"\b(wat|idee|ideeen|ideeën|lunch|ontbijt|tussendoor|hapje)", t):
            return self._baby_ideas(t)

        for pattern in REMOVE_PATTERNS:
            m = re.match(pattern, t)
            if m:
                return self._remove(week, m.group("items"))
        for pattern in ADD_PATTERNS:
            m = re.match(pattern, t)
            if m:
                items = parse_items(m.group("items"))
                if "mode" in m.groupdict() and m.group("mode"):
                    for it in items:
                        it.mode = "kiezen" if m.group("mode") == "kiezen" else "zelf"
                if items:
                    return self._add(week, speaker, items)

        is_question = re.search(r"\b(suggestie|suggesties|idee|ideeen|ideeën|wat (zullen|kunnen|gaan) we|"
                                r"wat eten we|wat maken we|iets)\b", t)
        for pattern in PLAN_PATTERNS:
            m = re.match(pattern, t)
            if m:
                dish = re.sub(r"^(?:de|het|een|lekker|weer)\s+", "", m.group("dish").strip())
                day = _day(m.group("day"))
                if is_question or not dish or dish.startswith("iets"):
                    return self._suggest(week, t, day)
                return self._plan(week, day, dish, speaker, lunch="lunch" in t)
        if is_question:
            m = re.search(DAY_RE, t)
            return self._suggest(week, t, _day(m.group("day")) if m else None)
        return None

    # ------------------------------------------------------------------
    def _add(self, week: str, speaker: str | None, items: list[Item]) -> Reply:
        out = []
        for it in items:
            row = self.app.shopping.add(week, it.name, it.quantity, it.unit, source="gesprek", added_by=speaker,
                                        mode=it.mode, organic=it.organic)
            label = row["name"] if it.quantity == 1 and not it.unit else f"{it.quantity:g}{' ' + it.unit if it.unit else '×'} {row['name']}"
            if row["mode"] != "auto":
                label += f" ({'zelf kiezen' if row['mode'] == 'kiezen' else 'zelf halen'})"
            out.append(label)
        return Reply("✓ Op de lijst: " + ", ".join(out) + ".", True)

    def _remove(self, week: str, text: str) -> Reply | None:
        removed, missing = [], []
        for it in parse_items(text):
            row = self.app.shopping.find(week, it.name)
            if row:
                self.app.shopping.remove(row["id"])
                removed.append(row["name"])
            else:
                missing.append(it.name)
        if not removed and not missing:
            return None
        msg = ("✓ Van de lijst: " + ", ".join(removed) + ".") if removed else ""
        if missing:
            msg += (" " if msg else "") + "Stond er niet op: " + ", ".join(missing) + "."
        return Reply(msg, bool(removed))

    def _plan(self, week: str, day: str, dish: str, speaker: str | None, lunch: bool = False) -> Reply:
        slot = "lunch" if lunch else "diner"
        meal = meals.get_meal(self.app.db, dish)
        if not meal:
            meals.plan_set(self.app.db, week, day, slot, title=dish)
            return Reply(f"✓ {DAY_NAMES[day].capitalize()}: {dish}. Dit gerecht ken ik niet, dus zet de "
                         "ingrediënten zelf op de lijst (of voeg het toe met een API-sleutel).", True)
        meals.plan_set(self.app.db, week, day, slot, meal_id=meal["id"])
        added = self.app.shopping.add_meal(week, meal, None, speaker)
        a = baby.adapt_meal(meal, self.app.baby_birthdate, profile=self.app.baby_profile)
        msg = (f"✓ {DAY_NAMES[day].capitalize()}: {meal['name']} (niveau {meal['level']}). "
               f"{len(added)} ingrediënten op de lijst.")
        pantry = self.app.shopping.pantry_check(meal)
        if pantry:
            msg += " Check de voorraadkast: " + ", ".join(pantry) + "."
        if a["steps"]:
            msg += f"\n🍼 {a['verdict']} – {a['steps'][0]}"
        return Reply(msg, True)

    def _suggest(self, week: str, t: str, day: str | None) -> Reply:
        level = _level(t)
        tags = _tags(t)
        max_minutes = 30 if re.search(r"\bsnel|vlug|weinig tijd\b", t) else None
        if level == 3:
            opts = meals.ready_meals(self.app.db, self.app.picnic)[:5]
            lines = [f"• {o['name']} ({euro(o['price_cents'])}{', bio' if o['is_organic'] else ''})" for o in opts]
        else:
            sug = meals.suggest(self.app.db, week, level=level, count=5, tags=[x for x in tags if x != "snel"] or None,
                                max_minutes=max_minutes, baby_months=self.app.baby_months,
                                baby_profile=self.app.baby_profile)
            lines = [f"• {s['name']} (niv. {s['level']}{', ' + str(s['prep_minutes']) + ' min' if s['prep_minutes'] else ''})"
                     + (f" – {s['reasons'][0]}" if s["reasons"] else "") for s in sug]
        if not lines and level in (1, 2):
            known = meals.likely_known(self.app.db)[:5]
            if known:
                return Reply("Ik weet nog niet welke gerechten jullie vaak maken. Op basis van jullie bestellingen "
                             "waarschijnlijk deze:\n" + "\n".join(f"• {k['meal']}" for k in known)
                             + "\nMarkeer ze onder Menu als ‘vaak’, dan komen ze hier terug.")
        if not lines:
            return Reply("Geen suggesties gevonden" + (" op dit niveau – markeer onder Menu welke gerechten jullie "
                                                         "vaak maken." if level in (1, 2) else "."))
        head = f"Ideeën{' voor ' + DAY_NAMES[day] if day else ''}{' (niveau ' + str(level) + ')' if level else ''}:"
        tail = f"\nZeg bv. “{DAY_NAMES[day] if day else 'woensdag'} {re.sub(r' [(].*$', '', lines[0][2:]).lower()}”."
        return Reply(head + "\n" + "\n".join(lines) + tail)

    def _show_list(self, week: str) -> Reply:
        items = self.app.shopping.items(week)
        if not items:
            return Reply("De lijst is nog leeg.")
        return Reply(f"Op de lijst ({len(items)}): " + ", ".join(i["name"] for i in items) + ".")

    def _deals(self) -> Reply:
        opps = deals.opportunities(self.app.db, self.app.settings, organic_level=self.app.profile()["organic_level"])
        if not opps:
            return Reply("Geen besparingskansen bekend – tik op Besparen → ‘Aanbiedingen verversen’.")
        return Reply("Goedkoper elders:\n" + "\n".join(
            f"• {o['product']['name']}: {o['store_name']} {o['deal']['title']} (−{euro(o['saving_cents'])})"
            for o in opps[:4]))

    def _baby_meal(self, dish: str) -> Reply:
        meal = meals.get_meal(self.app.db, dish)
        if not meal:
            warns = baby.check_ingredients([dish], self.app.baby_months, self.app.baby_profile["spice_ok"])
            return Reply("Dat gerecht ken ik niet. " + (" ".join(w["msg"] for w in warns) or
                                                         "Haal de portie eruit vóór het zouten."))
        a = baby.adapt_meal(meal, self.app.baby_birthdate, profile=self.app.baby_profile)
        lines = [f"🍼 {meal['name']}: {a['verdict']}"] + [f"• {s}" for s in a["steps"][:3]] + \
                [f"⚠︎ {w['msg']}" for w in a["warnings"][:2]]
        return Reply("\n".join(lines))

    def _baby_ideas(self, t: str) -> Reply:
        info = baby.ideas(self.app.baby_birthdate, profile=self.app.baby_profile)
        moments = [m for m in info["ideas"] if m in t] or list(info["ideas"])[:3]
        lines = [f"{m}: " + "; ".join(info["ideas"][m][:3]) for m in moments]
        return Reply(f"🍼 {info['stage']['label']}:\n" + "\n".join(lines))

    def _cart(self, week: str) -> Reply:
        res = self.app.shopping.push_to_cart(week)
        msg = f"✓ {len(res['added'])} producten in het Picnic-mandje."
        if res["skipped"]:
            msg += " Overgeslagen: " + ", ".join(f"{s['name']} ({s['reason']})" for s in res["skipped"][:6]) + "."
        return Reply(msg + " Afrekenen doe je zelf in de Picnic-app.", True)
