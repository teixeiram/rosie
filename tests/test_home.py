import json
from pathlib import Path

import httpx
import pytest

from rosie.sections.home import HomeConfig

CFG = """
dashboard_url = "https://ha.example/"
[[rooms]]
name = "Office"
tiles = [
  { entity = "light.office", label = "Office lights", kind = "toggle" },
  { entity = "script.morning", label = "Morning", kind = "script" },
  { entity = "input_boolean.vacation", label = "Vacation", kind = "toggle", confirm = true },
  { entity = "switch.secret", label = "Admin plug", kind = "toggle", admin = true },
]
[[rooms]]
name = "Downstairs"
tiles = [
  { entity = "climate.down", label = "Thermostat", kind = "thermostat", min = 60, max = 78 },
  { entity = "lock.garage", label = "Garage door", kind = "status" },
]
[[rooms]]
name = "Internet controls"
section = "internet"
tiles = [
  { entity = "switch.work_5g", label = "Work laptops on 5G", kind = "toggle", confirm = true },
]
"""

STATES = [
    {"entity_id": "light.office", "state": "on", "attributes": {}},
    {"entity_id": "script.morning", "state": "off", "attributes": {}},
    {"entity_id": "input_boolean.vacation", "state": "off", "attributes": {}},
    {"entity_id": "switch.secret", "state": "off", "attributes": {}},
    {
        "entity_id": "climate.down",
        "state": "heat",
        "attributes": {
            "current_temperature": 68,
            "temperature": 69,
            "min_temp": 45,
            "max_temp": 95,
        },
    },
    {"entity_id": "lock.garage", "state": "locked", "attributes": {}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
    {"entity_id": "switch.work_5g", "state": "off", "attributes": {}},
]


@pytest.fixture
def ha_calls(app, settings, tmp_path):
    cfg = tmp_path / "home.toml"
    cfg.write_text(CFG)
    app.state.settings = settings.__class__(
        **{**settings.__dict__, "ha_token": "secret-token", "home_config": cfg}
    )
    calls = []

    def handler(req: httpx.Request):
        calls.append(
            (
                req.method,
                req.url.path,
                req.headers.get("authorization"),
                json.loads(req.content) if req.content else None,
            )
        )
        if req.url.path == "/api/states":
            return httpx.Response(200, json=STATES)
        return httpx.Response(200, json=[])

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return calls


def test_status_shows_only_allowlisted(member, ha_calls):
    r = member.get("/api/home/status").json()
    ents = [t["entity"] for room in r["rooms"] for t in room["tiles"]]
    assert "lock.front_door" not in ents and "switch.secret" not in ents  # admin-only hidden
    thermo = r["rooms"][1]["tiles"][0]
    assert (thermo["min"], thermo["max"], thermo["current"]) == (60, 78, 68)
    assert "secret-token" not in json.dumps(r)
    assert ha_calls[0][2] == "Bearer secret-token"


def test_actions(member, ha_calls):
    ok = member.post("/api/home/action", json={"entity": "light.office", "action": "off"})
    assert ok.status_code == 200
    assert ha_calls[-1][1] == "/api/services/light/turn_off"
    assert ha_calls[-1][3] == {"entity_id": "light.office"}
    member.post("/api/home/action", json={"entity": "script.morning", "action": "run"})
    assert ha_calls[-1][1] == "/api/services/script/turn_on"
    r = member.post(
        "/api/home/action",
        json={"entity": "climate.down", "action": "set_temperature", "value": 70},
    )
    assert r.status_code == 200 and ha_calls[-1][3]["temperature"] == 70


def test_refusals(member, admin, ha_calls):
    def act(c, **kw):
        return c.post("/api/home/action", json=kw).status_code

    n = len(ha_calls)
    assert act(member, entity="lock.front_door", action="unlock") == 403  # not listed
    assert act(member, entity="lock.garage", action="on") == 403  # status only
    assert act(member, entity="light.office", action="run") == 403  # wrong action
    assert act(member, entity="switch.secret", action="on") == 403  # admin only
    assert act(member, entity="input_boolean.vacation", action="on") == 409  # needs confirm
    assert act(member, entity="climate.down", action="set_temperature", value=90) == 400
    assert len(ha_calls) == n  # nothing reached Home Assistant
    assert act(member, entity="input_boolean.vacation", action="on", confirmed=True) == 200
    assert act(admin, entity="switch.secret", action="on") == 200


def test_actions_are_logged(app, member, ha_calls):
    member.post("/api/home/action", json={"entity": "light.office", "action": "on"})
    row = app.state.db.execute("SELECT entity, action FROM home_actions").fetchone()
    assert tuple(row) == ("light.office", "on")


def test_not_configured(member):
    r = member.get("/api/home/status").json()
    assert r["configured"] is False
    assert (
        member.post(
            "/api/home/action", json={"entity": "light.office_lights", "action": "on"}
        ).status_code
        == 503
    )


def test_repo_home_toml_is_valid():
    cfg = HomeConfig.load(Path(__file__).parent.parent / "home.toml")
    tiles = cfg.tiles
    assert "input_boolean.vacation_mode" in tiles and tiles["input_boolean.vacation_mode"].confirm
    assert tiles["lock.garage_door"].kind == "status"
    assert not any(t.endswith(("modem_plug", "router_plug")) or "reboot" in t for t in tiles)


def test_bad_config_rejected(tmp_path):
    p = tmp_path / "h.toml"
    p.write_text('[[rooms]]\nname="x"\ntiles=[{entity="lock.door", label="d", kind="toggle"}]')
    with pytest.raises(ValueError):
        HomeConfig.load(p)


def test_thermostat_heat_cool_mode(member, ha_calls):
    auto = {
        "entity_id": "climate.down",
        "state": "heat_cool",
        "attributes": {
            "current_temperature": 67,
            "target_temp_low": 67,
            "target_temp_high": 71,
            "min_temp": 50,
            "max_temp": 90,
        },
    }
    STATES[4], saved = auto, STATES[4]
    try:
        t = member.get("/api/home/status").json()["rooms"][1]["tiles"][0]
        assert t["range"] is True and (t["low"], t["high"], t["target"]) == (67, 71, None)

        def act(**kw):
            return member.post(
                "/api/home/action",
                json={"entity": "climate.down", "action": "set_temperature", **kw},
            )

        assert act(low=66, high=72).status_code == 200
        assert ha_calls[-1][1] == "/api/services/climate/set_temperature"
        assert ha_calls[-1][3] == {
            "entity_id": "climate.down",
            "target_temp_low": 66,
            "target_temp_high": 72,
        }
        n = len(ha_calls)
        assert act(low=72, high=70).status_code == 400  # heat-to above cool-to
        assert act(low=70, high=70).status_code == 400
        assert act(low=55, high=70).status_code == 400  # below the app's 60
        assert act(low=65, high=80).status_code == 400  # above the app's 78
        assert act(low=65).status_code == 400  # needs both
        assert len(ha_calls) == n
    finally:
        STATES[4] = saved


def test_rooms_by_section(member, ha_calls):
    home = member.get("/api/home/status").json()
    assert "Internet controls" not in [r["name"] for r in home["rooms"]]
    net = member.get("/api/home/status?section=internet").json()
    assert [r["name"] for r in net["rooms"]] == ["Internet controls"]
    assert net["rooms"][0]["tiles"][0]["entity"] == "switch.work_5g"
    # Its tiles use the same action endpoint and confirm rule.
    r = member.post("/api/home/action", json={"entity": "switch.work_5g", "action": "on"})
    assert r.status_code == 409
    body = {"entity": "switch.work_5g", "action": "on", "confirmed": True}
    r = member.post("/api/home/action", json=body)
    assert r.status_code == 200 and ha_calls[-1][1] == "/api/services/switch/turn_on"


def test_unknown_section_rejected(tmp_path):
    cfg = tmp_path / "home.toml"
    cfg.write_text('[[rooms]]\nname = "X"\nsection = "garage"\ntiles = []\n')
    with pytest.raises(ValueError, match="unknown section"):
        HomeConfig.load(cfg)
