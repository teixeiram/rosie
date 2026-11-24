from fastapi.testclient import TestClient

from rosie import auth

from .conftest import PW


def test_password_hash_roundtrip():
    h = auth.hash_password("abcdefghijk")
    assert auth.check_password("abcdefghijk", h)
    assert not auth.check_password("abcdefghijx", h)
    assert not auth.check_password("x", "garbage")


def test_login_and_me(anon):
    assert anon.get("/api/me").status_code == 401
    r = anon.post("/api/login", json={"username": "mike", "password": "wrong-password"})
    assert r.status_code == 401
    r = anon.post("/api/login", json={"username": "MIKE", "password": PW})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = anon.get("/api/me").json()
    assert me["user"]["role"] == "admin"


def test_lockout_after_failures(anon):
    for _ in range(auth.MAX_FAILURES):
        assert (
            anon.post(
                "/api/login", json={"username": "sam", "password": "nope-nope-nope"}
            ).status_code
            == 401
        )
    r = anon.post("/api/login", json={"username": "sam", "password": PW})
    assert r.status_code == 429


def test_logout_ends_session(admin):
    assert admin.post("/api/logout").status_code == 200
    assert admin.get("/api/me").status_code == 401


def test_set_password_signs_out(app, member):
    auth.set_password(app.state.db, "sam", "a brand new password")
    assert member.get("/api/me").status_code == 401


def test_member_cannot_see_or_call_developer(member, admin):
    ids = [s["id"] for s in member.get("/api/me").json()["sections"]]
    assert "developer" not in ids and "kitchen" in ids
    assert member.get("/api/developer/status").status_code == 403
    assert admin.get("/api/developer/status").status_code == 200
    assert "developer" in [s["id"] for s in admin.get("/api/me").json()["sections"]]


def test_csrf_header_required(app):
    with TestClient(app) as c:  # no X-Requested-With header
        r = c.post("/api/login", json={"username": "mike", "password": PW})
        assert r.status_code == 403
    with TestClient(
        app, headers={"X-Requested-With": "fetch", "Origin": "https://evil.example"}
    ) as c:
        r = c.post("/api/login", json={"username": "mike", "password": PW})
        assert r.status_code == 403


def test_security_headers_and_shell(anon):
    r = anon.get("/")
    assert r.status_code == 200
    assert "<title>TestApp</title>" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    m = anon.get("/manifest.webmanifest").json()
    assert m["name"] == "TestApp" and "share_target" in m


def test_broken_section_does_not_take_app_down(settings, monkeypatch):
    from rosie import app as appmod

    monkeypatch.setattr(
        appmod, "SECTIONS", appmod.SECTIONS + [("rosie.sections.nope", "router", "SECTION")]
    )
    a = appmod.create_app(settings)
    auth.create_user(a.state.db, "mike", "Mike", "admin", PW)
    with TestClient(a, headers={"X-Requested-With": "fetch"}) as c:
        c.post("/api/login", json={"username": "mike", "password": PW})
        secs = {s["id"]: s["ok"] for s in c.get("/api/me").json()["sections"]}
    assert secs["nope"] is False and secs["kitchen"] is True
