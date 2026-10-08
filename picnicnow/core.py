"""Alles bij elkaar: instellingen, database, Picnic, lijst en het huishoudprofiel."""

from __future__ import annotations

from datetime import date

from . import baby, combine, meals
from .config import Settings
from .db import Database
from .picnic import PicnicService, make_picnic
from .shopping import ShoppingList
from .text import iso_week, next_week

PROFILE_FIELDS = ("names", "baby_name", "baby_birthdate", "persons", "organic_level", "diet_notes",
                  "kitchen_notes", "budget_week")


class App:
    def __init__(self, settings: Settings | None = None, db: Database | None = None,
                 picnic: PicnicService | None = None):
        self.settings = settings or Settings.from_env()
        self.db = db or Database(self.settings.db_path)
        self.picnic = picnic or make_picnic(self.settings, self.db)
        self.shopping = ShoppingList(self.db, self.picnic, self.settings)
        meals.seed_recipes(self.db)

    # --- profiel -------------------------------------------------------------
    def profile(self) -> dict:
        s = self.settings
        base = {
            "names": s.names,
            "baby_name": s.baby_name,
            "baby_birthdate": s.baby_birthdate.isoformat() if s.baby_birthdate else None,
            "persons": s.persons,
            "organic_level": s.organic_level,
            "diet_notes": "",
            "kitchen_notes": "",
            "budget_week": None,
        }
        base.update({k: v for k, v in (self.db.get_pref("profile", {}) or {}).items() if k in PROFILE_FIELDS})
        return base

    def update_profile(self, **changes) -> dict:
        current = self.db.get_pref("profile", {}) or {}
        for k, v in changes.items():
            if k in PROFILE_FIELDS and v is not None:
                current[k] = v
        self.db.set_pref("profile", current)
        if "organic_level" in current:
            self.db.set_pref("organic_level", int(current["organic_level"]))
        return self.profile()

    @property
    def baby_birthdate(self) -> date | None:
        value = self.profile().get("baby_birthdate")
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    @property
    def baby_months(self) -> int | None:
        return baby.age_months(self.baby_birthdate)

    # --- week ------------------------------------------------------------------
    def active_week(self) -> str:
        week = self.db.get_pref("active_week")
        if week:
            return week
        today = date.today()
        # vanaf donderdag plan je meestal de volgende week
        return next_week(iso_week(today)) if today.weekday() >= 3 else iso_week(today)

    def set_week(self, week: str) -> str:
        self.db.set_pref("active_week", week)
        return week

    # --- snapshot voor UI en assistent -------------------------------------------
    def state(self, week: str | None = None) -> dict:
        week = week or self.active_week()
        plan = meals.plan_get(self.db, week)
        for row in plan:
            if row["meal"] and row["slot"] == "diner":
                row["baby"] = baby.adapt_meal(row["meal"], self.baby_birthdate)
        return {
            "week": week,
            "profile": self.profile(),
            "demo": self.picnic.demo,
            "assistant": self.settings.assistant_enabled,
            "plan": plan,
            "list": self.shopping.items(week),
            "summary": self.shopping.summary(week),
            "tips": combine.combine_week(self.db, week, self.baby_months),
            "baby_stage": baby.stage(self.baby_birthdate),
            "mode_rules": self.shopping.mode_rules(),
            "last_sync": self.db.get_pref("last_sync"),
            "deals_refreshed": self.db.get_pref("deals_refreshed"),
        }

    def compact_state(self, week: str | None = None) -> str:
        """Korte, leesbare samenvatting voor in het gesprek met de assistent."""
        week = week or self.active_week()
        plan = meals.plan_get(self.db, week)
        dinners = [f"{r['day']} {r['slot']}: {(r['meal'] or {}).get('name') or r['title']}" for r in plan]
        items = self.shopping.items(week)
        lst = ", ".join(f"{i['name']}{' (' + i['mode'] + ')' if i['mode'] != 'auto' else ''}" for i in items[:40])
        more = f" (+{len(items) - 40})" if len(items) > 40 else ""
        return (f"[Stand week {week}] Menu: {'; '.join(dinners) or 'nog leeg'}. "
                f"Lijst ({len(items)}): {lst or 'leeg'}{more}.")
