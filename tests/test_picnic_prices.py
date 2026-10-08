from types import SimpleNamespace

from python_picnic_api2 import SearchResultItem

from picnicnow.db import Database
from picnicnow.picnic import RealPicnic, plausible_price, tile_price


def tile(**fields):
    raw = fields.pop("raw", None)
    item = SearchResultItem.model_validate({"id": "s1", "name": "Halfvolle melk", **fields})
    item.raw = raw
    return item


def test_plausible_price():
    assert plausible_price(129) == 129
    assert plausible_price(1035.0) == 1035
    assert plausible_price(432199) is None
    assert plausible_price(0) is None and plausible_price(None) is None and plausible_price(True) is None


def test_tile_price_skips_placeholder():
    assert tile_price(tile(display_price=129)) == 129
    assert tile_price(tile(display_price=432199, price_ranges=[{"price": 99.0, "from_quantity": 1}])) == 99
    raw = {"type": "SELLING_UNIT_TILE", "child": [{"type": "PRICE", "price": 349, "isCrossed": True},
                                                  {"type": "PRICE", "price": 249}]}
    assert tile_price(tile(display_price=432199, raw=raw)) == 249
    assert tile_price(tile(display_price=432199)) is None


class FakeApi:
    def __init__(self, items):
        self.items, self.article_calls = items, 0

    def logged_in(self):
        return True

    def search(self, term):
        return SimpleNamespace(items=self.items)

    def get_article(self, article_id):
        self.article_calls += 1
        return SimpleNamespace(price=189)


def test_search_falls_back_to_product_page():
    picnic = RealPicnic("u", "p")
    picnic.api = FakeApi([tile(display_price=432199), tile(id="s2", display_price=129)])
    prices = [p.price_cents for p in picnic.search("melk")]
    assert prices == [189, 129]
    picnic.search("melk")
    assert picnic.api.article_calls == 1  # gecachet


def test_db_cleans_placeholder_prices(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    db.execute("INSERT INTO products(id, name, price_cents) VALUES ('s1', 'Melk', 432199)")
    db._conn.close()
    assert Database(path).one("SELECT price_cents FROM products WHERE id='s1'")["price_cents"] is None
