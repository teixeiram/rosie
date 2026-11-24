import pytest
from fastapi.testclient import TestClient

from rosie import auth
from rosie.app import create_app
from rosie.config import Branding, Settings

PW = "correct horse battery"


def make_settings(tmp_path, **kw):
    base = dict(
        db_path=tmp_path / "app.db",
        branding=Branding(name="TestApp"),
        cookie_secure=False,
        anthropic_api_key="test-key",
        recipe_topic="recipes-" + "a" * 24,
        recipe_secret="b" * 64,
        prometheus_url="http://prom.test",
        grafana_url="http://grafana.test",
        monthly_cap_usd=1.0,
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    app = create_app(settings)
    auth.create_user(app.state.db, "mike", "Mike", "admin", PW)
    auth.create_user(app.state.db, "sam", "Sam", "member", PW)
    return app


def _client(app, username=None):
    c = TestClient(app, headers={"X-Requested-With": "fetch"})
    c.__enter__()
    if username:
        r = c.post("/api/login", json={"username": username, "password": PW})
        assert r.status_code == 200, r.text
    return c


@pytest.fixture
def anon(app):
    c = _client(app)
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def admin(app):
    c = _client(app, "mike")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def member(app):
    c = _client(app, "sam")
    yield c
    c.__exit__(None, None, None)
