"""Tekst-hulpjes: normaliseren, fuzzy matchen, bio herkennen, categoriseren."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher

DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
DAY_NAMES = {
    "ma": "maandag", "di": "dinsdag", "wo": "woensdag", "do": "donderdag",
    "vr": "vrijdag", "za": "zaterdag", "zo": "zondag",
}

ORGANIC_MARKERS = (
    "bio", "biologisch", "biologische", "organic", "eko", "ekoplaza", "demeter",
    "de nieuwe band", "zonnatura", "natural cooking", "biodora", "rapunzel",
    "holle", "hipp", "ella's kitchen", "ellas kitchen", "bonneterre", "bio+",
    "zuivelhoeve bio", "landgoed", "de groene weg", "naturli",
)
_ORGANIC_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(m) for m in ORGANIC_MARKERS) + r")(?![a-z])"
)

# Woorden die bij matchen weinig zeggen.
STOPWORDS = {
    "de", "het", "een", "van", "met", "en", "of", "voor", "in", "op", "per", "stuk",
    "stuks", "gram", "g", "kg", "ml", "l", "liter", "pak", "zak", "bak", "doos",
    "picnic", "verse", "vers", "ca", "x",
    # bio-labels zeggen niets over wát het product is (bio-voorkeur wordt apart gewogen)
    "bio", "biologisch", "biologische", "eko", "organic",
}

# Eenvoudige winkelindeling (volgorde = looproute / weergave).
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Groente & fruit", (
        "tomaat", "tomaten", "komkommer", "paprika", "ui", "uien", "knoflook", "wortel", "wortelen",
        "broccoli", "bloemkool", "spinazie", "sla", "rucola", "courgette", "aubergine", "prei",
        "champignon", "champignons", "avocado", "appel", "appels", "banaan", "bananen", "peer",
        "peren", "aardbei", "aardbeien", "blauwe bes", "blauwe bessen", "framboos", "druif",
        "druiven", "citroen", "limoen", "sinaasappel", "mandarijn", "mango", "kiwi", "pompoen",
        "zoete aardappel", "aardappel", "aardappelen", "krieltjes", "boerenkool", "andijvie",
        "spruitjes", "sperziebonen", "doperwten", "mais", "gember", "peterselie", "koriander",
        "basilicum", "bieslook", "dille", "munt", "bosui", "lente-ui", "venkel", "bieten",
        "biet", "pastinaak", "knolselderij", "selderij", "radijs", "paksoi", "taugé", "rode kool",
        "witlof", "snoeptomaatjes", "cherrytomaten", "kers", "meloen", "ananas", "groente", "fruit",
    )),
    ("Vlees, vis & vega", (
        "kip", "kipfilet", "kippendij", "gehakt", "rundergehakt", "varkenshaas", "biefstuk",
        "spek", "spekjes", "worst", "rookworst", "zalm", "kabeljauw", "vis", "garnalen",
        "tonijn", "tofu", "tempeh", "vegaburger", "vegetarisch", "kalkoen", "lamsvlees",
        "shoarma", "hamburger", "ham", "kipgehakt", "pollak", "koolvis", "makreel",
    )),
    ("Zuivel & eieren", (
        "melk", "yoghurt", "kwark", "kaas", "boter", "room", "slagroom", "crème fraîche",
        "creme fraiche", "zure room", "ei", "eieren", "mozzarella", "feta", "parmezaan",
        "ricotta", "mascarpone", "halfvolle", "volle", "skyr", "geitenkaas", "hüttenkäse",
    )),
    ("Brood & ontbijt", (
        "brood", "volkorenbrood", "wraps", "wrap", "pita", "crackers", "beschuit", "havermout",
        "muesli", "granola", "ontbijtkoek", "croissant", "stokbrood", "tortilla",
    )),
    ("Voorraad", (
        "pasta", "spaghetti", "penne", "macaroni", "rijst", "couscous", "bulgur", "quinoa",
        "linzen", "kikkererwten", "bonen", "kidneybonen", "zwarte bonen", "tomatenblokjes",
        "passata", "tomatenpuree", "kokosmelk", "bouillon", "olie", "olijfolie", "azijn",
        "sojasaus", "meel", "bloem", "suiker", "zout", "peper", "kruiden", "paprikapoeder",
        "komijn", "kerrie", "curry", "pindakaas", "noedels", "mie", "gnocchi", "pesto",
        "notenpasta", "tahin", "honing", "jam", "rozijnen", "noten", "zaden", "lasagnebladen",
    )),
    ("Baby", ("babyhapje", "babyvoeding", "luiers", "billendoekjes", "knijpfruit", "flesvoeding", "opvolgmelk")),
    ("Diepvries", ("diepvries", "ijs", "doperwtjes diepvries", "spinazie diepvries")),
    ("Drinken", ("koffie", "thee", "sap", "water", "bier", "wijn", "frisdrank", "spa")),
    ("Huishouden", ("wc-papier", "keukenrol", "afwasmiddel", "wasmiddel", "vaatwastabletten", "zeep", "tandpasta")),
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' +-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_SYNONYMS = {"ei": "eier", "eitje": "eier", "eitjes": "eier", "uitje": "ui", "uitjes": "ui"}


def _stem(word: str) -> str:
    """Heel grove Nederlandse stam, zodat enkelvoud en meervoud samenvallen:
    tomaat/tomaten → tomat, boon/bonen → bon, eieren → eier."""
    if word in _SYNONYMS:
        return _SYNONYMS[word]
    for suffix in ("'s", "en", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return re.sub(r"([aeou])\1", r"\1", word)


def tokens(text: str) -> set[str]:
    return {_stem(t) for t in normalize(text).split() if t not in STOPWORDS and len(t) > 1}


def similarity(query: str, candidate: str) -> float:
    """0..1: hoeveel van de zoekterm terugkomt in de kandidaat (+ tekengelijkenis)."""
    q, c = tokens(query), tokens(candidate)
    if not q or not c:
        return 0.0
    overlap = len(q & c) / len(q)
    # gedeeltelijke woordmatch: zoekwoord ín een samenstelling telt volledig
    # ('brood' in 'volkorenbrood'), andersom maar half ('kaas' in 'pindakaas').
    def part(t: str) -> float:
        # samenstellingen: het woord zit aan het begin of eind ('tomaten|blokjes', 'tros|tomaten'),
        # niet ergens middenin ('oma' in 't-oma-tensoep' telt dus niet)
        if any(len(t) > 2 and (w.startswith(t) or w.endswith(t)) for w in c):
            return 1.0
        if any(w.startswith(t) and len(w) <= len(t) + 3 for w in c):
            return 1.0
        return 0.5 if any(len(w) > 3 and (t.startswith(w) or t.endswith(w)) for w in c) else 0.0

    partial = sum(part(t) for t in q) / len(q)
    ratio = SequenceMatcher(None, normalize(query), normalize(candidate)).ratio()
    return round(max(overlap, 0.85 * partial) * 0.8 + ratio * 0.2, 4)


STORE_BRANDS = {"ah", "albert", "heijn", "g'woon", "gwoon", "1", "beste", "vomar", "deka", "dekamarkt",
                "jumbo", "plus", "picnic", "biologisch", "biologische", "bio", "excellent", "basic", "huismerk"}


def coverage(candidate: str, query: str) -> float:
    """Welk deel van de woorden van de kandidaat terugkomt in de zoekterm (merknamen genegeerd).
    Laag = de kandidaat is iets anders/specifiekers ('kipfilet roasted' vs 'kipfilet')."""
    c = {t for t in tokens(candidate) if t not in STORE_BRANDS and not t.isdigit()}
    q = tokens(query)
    if not c:
        return 1.0
    hit = sum(1 for t in c if any(t == w or (len(w) > 3 and (t.startswith(w) or t.endswith(w)
                                                           or w.startswith(t) or w.endswith(t))) for w in q))
    return hit / len(c)


_SIZE_NUM = re.compile(r"\b(maat|size|nr|nummer|stap)\s*(\d+)", re.I)


def same_variant(a: str, b: str) -> bool:
    """'Luiers maat 4' is niet hetzelfde als 'luiers maat 3'."""
    ma, mb = _SIZE_NUM.search(a or ""), _SIZE_NUM.search(b or "")
    if not ma:
        return True
    return bool(mb) and ma.group(2) == mb.group(2)


def is_organic(name: str, extra: str = "") -> bool:
    return bool(_ORGANIC_RE.search(normalize(f"{name} {extra}")))


def categorize(name: str) -> str:
    n = f" {normalize(name)} "
    best, best_len = "Overig", 0
    for category, words in CATEGORIES:
        for w in words:
            if f" {w} " in n or (len(w) >= 4 and w in n):
                if len(w) > best_len:
                    best, best_len = category, len(w)
    return best


def category_order(category: str) -> int:
    names = [c for c, _ in CATEGORIES]
    return names.index(category) if category in names else len(names)


def iso_week(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def next_week(week: str) -> str:
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return iso_week(monday + timedelta(days=7))


def week_monday(week: str) -> date:
    y, w = week.split("-W")
    return date.fromisocalendar(int(y), int(w), 1)


def euro(cents: int | float | None) -> str:
    if cents is None:
        return "?"
    return f"€{cents / 100:.2f}".replace(".", ",")
