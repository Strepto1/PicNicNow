from picnicnow.text import categorize, coverage, is_organic, iso_week, next_week, same_variant, similarity


def test_singular_plural_and_compounds_match():
    assert similarity("tomaat", "Picnic trostomaten") > 0.7
    assert similarity("eieren", "Biologische scharreleieren") > 0.7
    assert similarity("ui", "Uien") > 0.7
    assert similarity("brood", "Biologisch volkorenbrood heel") > 0.7


def test_organic_label_does_not_drive_similarity():
    assert similarity("Biologische bananen", "Biologische peren") < 0.3
    assert similarity("brood", "Paprika rood") < 0.5


def test_organic_detection():
    assert is_organic("Biologische trostomaten")
    assert is_organic("Hipp groentehapje")
    assert not is_organic("Biscuitjes")


def test_categorize():
    assert categorize("Biologische kipfilet") == "Vlees, vis & vega"
    assert categorize("zalmfilet") == "Vlees, vis & vega"
    assert categorize("halfvolle melk") == "Zuivel & eieren"
    assert categorize("pindakaas") == "Voorraad"


def test_variants_and_coverage():
    assert not same_variant("Luiers maat 4", "Luiers midi maat 3")
    assert same_variant("Luiers maat 4", "Luiers maxi maat 4")
    assert coverage("Roosterz & Co Kipfilet roasted", "kipfilet") < 0.5
    assert coverage("AH Biologisch Kipfilet", "biologische kipfilet") == 1


def test_weeks():
    assert next_week("2026-W52") == "2026-W53"
    assert next_week("2026-W53") == "2027-W01"
    assert iso_week().startswith("20")
