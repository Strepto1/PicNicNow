"""Slim combineren: restjes benutten, één keer koken twee keer eten, baby-batches, variatie."""

from __future__ import annotations

from collections import defaultdict

from . import baby as baby_mod
from .db import Database
from .meals import is_perishable, leftover_rank as _leftover_rank, list_meals, plan_get
from .text import DAY_NAMES, normalize, similarity


def _same(a: str, b: str) -> bool:
    return similarity(a, b) >= 0.7 or normalize(a) == normalize(b)


def combine_week(db: Database, week: str, baby_months: int | None = None,
                 baby_profile: dict | None = None) -> list[dict]:
    plan = [r for r in plan_get(db, week) if r["meal"]]
    tips: list[dict] = []
    if not plan:
        return tips

    # 1. Ingrediënten die in meerdere gerechten terugkomen -> samen kopen
    uses: dict[str, list[str]] = defaultdict(list)
    canonical: dict[str, str] = {}
    for r in plan:
        for ing in r["meal"]["ingredients"]:
            if ing.get("pantry"):
                continue
            key = next((k for k in canonical if _same(k, ing["name"])), ing["name"])
            canonical.setdefault(key, ing["name"])
            if r["meal"]["name"] not in uses[key]:
                uses[key].append(r["meal"]["name"])
    for ing, meals in uses.items():
        if len(meals) >= 2 and is_perishable(ing):
            tips.append({"type": "samen", "title": f"{ing.capitalize()} komt {len(meals)}x terug",
                         "detail": "Koop één grotere verpakking voor: " + ", ".join(meals) + ".",
                         "ingredient": ing})

    # 2. Bederfelijke 'wezen' -> gerecht zoeken dat het restje opmaakt.
    #    Vlees/vis koop je per portie; restjes ontstaan vooral bij kruiden, zuivel en groente.
    planned_ids = {r["meal"]["id"] for r in plan}
    library = [m for m in list_meals(db) if m["id"] not in planned_ids and m["kind"] == "zelf"]
    orphans = [ing for ing, meals in uses.items()
               if len(meals) == 1 and is_perishable(ing) and _leftover_rank(ing) < 9]
    orphans.sort(key=_leftover_rank)
    for ing in orphans[:3]:
        meals = uses[ing]
        matches = [m for m in library if any(_same(ing, i["name"]) for i in m["ingredients"])]
        if matches:
            matches.sort(key=lambda m: (m["level"], m.get("prep_minutes") or 99))
            tips.append({"type": "restje", "title": f"Restje {ing}?",
                         "detail": f"{ing.capitalize()} zit alleen in {meals[0]}. Combineer met: "
                                   + ", ".join(m["name"] for m in matches[:2]) + ".",
                         "ingredient": ing, "meal_ids": [m["id"] for m in matches[:2]]})

    # 3. Eén keer koken, twee keer eten
    for r in plan:
        extra = r["meal"].get("makes_extra")
        if not extra:
            continue
        followers = [m for m in library if m.get("uses_leftover") == extra]
        planned_followers = [p for p in plan if p["meal"].get("uses_leftover") == extra]
        day = DAY_NAMES.get(r["day"], r["day"])
        if planned_followers:
            tips.append({"type": "dubbel", "title": f"Kook extra {extra} op {day}",
                         "detail": f"Dan heb je meteen de basis voor {planned_followers[0]['meal']['name']}."})
        elif followers:
            tips.append({"type": "dubbel", "title": f"{r['meal']['name']}: maak dubbel {extra}",
                         "detail": f"Restje {extra} wordt de dag erna: " + " of ".join(f["name"] for f in followers[:2]) + ".",
                         "meal_ids": [f["id"] for f in followers[:2]]})
        else:
            tips.append({"type": "dubbel", "title": f"Restje {extra} van {day}",
                         "detail": "Neem het mee als lunch de volgende dag (of vries een babyportie in)."})

    # 4. Baby-batch: groente die deze week toch al gekookt wordt
    if baby_months is None or baby_months >= 5:
        veg = []
        for ing in uses:
            hits = baby_mod.check_ingredients([ing], baby_months)
            if not hits and is_perishable(ing) and any(k in normalize(ing) for k in (
                    "pompoen", "broccoli", "courgette", "wortel", "zoete aardappel", "bloemkool",
                    "sperziebonen", "doperwten", "pastinaak", "avocado", "paprika")):
                veg.append(ing)
        if veg:
            tips.append({"type": "baby", "title": "Baby-batch meekoken",
                         "detail": "Stoom/kook wat extra " + ", ".join(veg[:4])
                                   + " zonder zout, pureer en vries in ijsblokjesvorm in: babylunches voor de hele week."})

    # 5. Variatie & balans
    tag_count: dict[str, int] = defaultdict(int)
    for r in plan:
        for t in r["meal"]["tags"]:
            tag_count[t] += 1
    if tag_count.get("pasta", 0) >= 3:
        tips.append({"type": "balans", "title": "Veel pasta deze week",
                     "detail": "Wissel er één om voor een gerecht met peulvruchten of aardappel."})
    if not tag_count.get("vis") and len(plan) >= 4:
        fish = [m for m in library if "vis" in m["tags"]][:2]
        tips.append({"type": "balans", "title": "Nog geen vis",
                     "detail": "Advies: 1x per week (vette) vis – ook goed voor baby. Bijvoorbeeld: "
                               + ", ".join(m["name"] for m in fish) + ".",
                     "meal_ids": [m["id"] for m in fish]})
    bp = baby_profile or {}
    if bp.get("avg") == "af en toe" and not tag_count.get("avg") and len(plan) >= 4:
        avg = sorted([m for m in library if "avg" in m["tags"]],
                     key=lambda m: (0 if {"mediterraan", "kindvriendelijk"} & set(m["tags"]) else 1, m["level"]))[:3]
        name = bp.get("name") or "de baby"
        tips.append({"type": "baby", "title": "Eén keer aardappel-groente-vlees?",
                     "detail": f"{name} is geen AVG-fan, maar het is goed om eraan te blijven wennen. "
                               "Op smaak gebracht gaat het makkelijker: " + ", ".join(m["name"] for m in avg) + ".",
                     "meal_ids": [m["id"] for m in avg]})
    vega = tag_count.get("vega", 0)
    if len(plan) >= 5 and vega < 2:
        tips.append({"type": "balans", "title": f"{vega} vegetarische avond(en)",
                     "detail": "Twee vega-avonden met peulvruchten scheelt geld en is goed voor ijzer & vezels."})
    return tips
