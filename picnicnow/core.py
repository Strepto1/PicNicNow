"""Alles bij elkaar: instellingen, database, Picnic, lijst en het huishoudprofiel."""

from __future__ import annotations

from datetime import date, timedelta

from . import baby, combine, meals, usage
from .config import Settings
from .db import Database
from .picnic import PicnicService, make_picnic
from .shopping import ShoppingList
from .text import iso_week, next_week

PROFILE_FIELDS = ("names", "baby_name", "baby_birthdate", "persons", "organic_level", "diet_notes",
                  "kitchen_notes", "budget_week", "baby_spice_ok", "baby_likes", "baby_avg", "baby_notes",
                  "model", "monthly_budget_usd")


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
            "baby_spice_ok": s.baby_spice_ok,
            "baby_likes": s.baby_likes,
            "baby_avg": s.baby_avg,
            "baby_notes": "",
            "model": s.model,
            "monthly_budget_usd": s.monthly_budget_usd,
        }
        stored = self.db.get_pref("profile", {}) or {}
        if not stored.get("baby_birthdate") and not base["baby_birthdate"] and s.baby_age_months is not None:
            # alleen een leeftijd opgegeven: één keer omrekenen naar een (geschatte) geboortedatum,
            # zodat de leeftijd daarna vanzelf meegroeit
            stored["baby_birthdate"] = (date.today() - timedelta(days=round(s.baby_age_months * 30.44))).isoformat()
            self.db.set_pref("profile", stored)
        base.update({k: v for k, v in stored.items() if k in PROFILE_FIELDS})
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

    @property
    def baby_profile(self) -> dict:
        p = self.profile()
        return {"name": p["baby_name"], "spice_ok": bool(p.get("baby_spice_ok")),
                "likes": p.get("baby_likes") or "", "avg": p.get("baby_avg") or "normaal",
                "notes": p.get("baby_notes") or ""}

    # --- assistent: Claude of gratis commandomodus ------------------------------
    def assistant_status(self) -> dict:
        p = self.profile()
        budget = p.get("monthly_budget_usd")
        spent = usage.month_summary(self.db)
        if not self.settings.has_api_key:
            mode, reason = "commando", "geen API-sleutel"
        elif self.settings.assistant_mode == "commando":
            mode, reason = "commando", "commandomodus ingesteld"
        elif usage.over_budget(self.db, budget):
            mode, reason = "commando", f"maandbudget van ${budget:.2f} bereikt"
        else:
            mode, reason = "claude", ""
        return {"mode": mode, "reason": reason, "model": p.get("model") or self.settings.model,
                "budget_usd": budget, "usage": spent, "models": usage.MODEL_CHOICES}

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
                row["baby"] = baby.adapt_meal(row["meal"], self.baby_birthdate, profile=self.baby_profile)
        return {
            "week": week,
            "profile": self.profile(),
            "demo": self.picnic.demo,
            "assistant": self.assistant_status(),
            "plan": plan,
            "list": self.shopping.items(week),
            "summary": self.shopping.summary(week),
            "tips": combine.combine_week(self.db, week, self.baby_months, self.baby_profile),
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
