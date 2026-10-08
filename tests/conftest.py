import pytest

from picnicnow.config import Settings
from picnicnow.core import App
from picnicnow.db import Database
from picnicnow.history import sync_history
from picnicnow.picnic import DemoPicnic


@pytest.fixture
def app(tmp_path):
    settings = Settings(names=["Kevin", "Sanne"], db_path=tmp_path / "t.db", deal_stores=["ah", "vomar"])
    a = App(settings=settings, db=Database(), picnic=DemoPicnic())
    sync_history(a.db, a.picnic)
    return a


@pytest.fixture
def week(app):
    return app.active_week()
