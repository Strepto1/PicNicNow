import httpx

from picnicnow.deals import service
from picnicnow.deals.ah import AlbertHeijn, parse_product
from picnicnow.deals.base import Deal, effective_price, to_cents
from picnicnow.deals.html import parse_offers_page
from picnicnow.deals.stores import Vomar, parse_dirk_offers


def test_prices():
    assert to_cents("€ 2,49") == 249
    assert to_cents(2.49) == 249
    assert to_cents(3) == 300
    assert effective_price(Deal("ah", "x", price_cents=400, label="1+1 gratis")) == 200
    assert effective_price(Deal("ah", "x", price_cents=400, label="2e halve prijs")) == 300
    assert effective_price(Deal("ah", "x", price_cents=400, label="25% korting")) == 300
    # actieprijs al verrekend -> niet nogmaals korting
    assert effective_price(Deal("ah", "x", price_cents=300, original_price_cents=400, label="25% korting")) == 300


def test_ah_parse_only_bonus():
    assert parse_product({"title": "AH Melk", "isBonus": False}) is None
    d = parse_product({"webshopId": 1, "title": "AH Biologisch Kipfilet", "salesUnitSize": "400 g",
                       "currentPrice": 6.29, "priceBeforeBonus": 8.39, "isBonus": True,
                       "bonusMechanism": "25% korting"})
    assert d.price_cents == 629 and d.original_price_cents == 839 and d.is_organic


def test_html_jsonld_nextdata_and_cards():
    jsonld = '<script type="application/ld+json">{"@type":"Product","name":"Bio koffiebonen 500 g","offers":{"price":"7.49"}}</script>'
    assert parse_offers_page(jsonld, "vomar")[0].price_cents == 749
    nxt = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"offers":[{"title":"Zalmfilet",'
           '"price":{"amount":4.99},"oldPrice":7.49,"label":"1/3 korting","unit":"250 g"}]}}</script>')
    d = parse_offers_page(nxt, "dekamarkt")[0]
    assert (d.title, d.price_cents, d.original_price_cents, d.unit) == ("Zalmfilet", 499, 749, "250 g")
    cards = ('<div class="offer-card"><h3>Biologische bananen</h3><span class="label">2e halve prijs</span>'
             '<span>2,49</span><a href="/p/1">x</a></div>')
    c = parse_offers_page(cards, "vomar", "https://www.vomar.nl")[0]
    assert c.title == "Biologische bananen" and c.label == "2e halve prijs" and c.url.endswith("/p/1")


def test_dirk_app_offers():
    d = parse_dirk_offers([{"title": "Kipfilet", "discountPrice": 4.99, "originalPrice": 6.99,
                            "priceLabel": "2 voor", "descriptiveSize": "500 g", "products": []}])[0]
    assert d.price_cents == 499 and d.unit == "500 g"


def _ah_client():
    def handler(req):
        if req.url.path.endswith("anonymous"):
            return httpx.Response(200, json={"access_token": "t"})
        q = req.url.params["query"]
        prods = []
        if "kipfilet" in q:
            prods = [{"webshopId": 1, "title": "AH Biologisch Kipfilet", "salesUnitSize": "400 g", "currentPrice": 6.29,
                      "priceBeforeBonus": 8.39, "isBonus": True, "bonusMechanism": "25% korting",
                      "propertyIcons": ["biologisch"]},
                     {"webshopId": 3, "title": "AH Kipfilet", "salesUnitSize": "400 g", "currentPrice": 3.0,
                      "priceBeforeBonus": 4.5, "isBonus": True, "bonusMechanism": "1/3 korting"}]
        return httpx.Response(200, json={"products": prods})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_opportunities_respect_organic(app):
    vomar = Vomar(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    res = service.refresh_deals(app.db, app.settings, [AlbertHeijn(_ah_client()), vomar])
    assert res["ah"]["deals"] == 2 and res["vomar"]["deals"] == 0
    opps = service.opportunities(app.db, app.settings, organic_level=2)
    kip = [o for o in opps if o["product"]["name"] == "Biologische kipfilet"]
    assert kip and kip[0]["deal"]["title"] == "AH Biologisch Kipfilet"   # niet de gangbare kip
    assert kip[0]["saving_cents"] == 849 - 629


def test_watchlist_only_frequent_and_pricey(app):
    wl = service.watchlist(app.db, app.settings)
    assert wl and all(w["times"] >= 2 and w["price_cents"] >= app.settings.deal_min_price_cents for w in wl)
