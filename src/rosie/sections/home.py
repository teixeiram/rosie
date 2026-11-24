"""Home: curated Home Assistant controls (issue #7).

Only entities and actions listed in home.toml are allowed; the server checks
every request against it (the browser's view is never trusted). The Home
Assistant token stays in .env on the server. Every action is logged with who
did it. Without HA_TOKEN the section says it isn't set up yet.
"""

from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import User, current_user

SECTION = {"id": "home", "title": "Home", "admin_only": False}
router = APIRouter(prefix="/api/home", tags=["home"])

KINDS = {"toggle", "script", "thermostat", "status"}
SECTIONS = {"home", "internet"}  # where a [[rooms]] entry is shown (key "section")
TOGGLE_DOMAINS = {"light", "switch", "fan", "input_boolean"}
ON_STATES = {"on", "open", "unlocked", "playing", "heat", "cool", "heat_cool", "auto"}


@dataclass(frozen=True)
class Tile:
    entity: str
    label: str
    kind: str
    confirm: bool = False
    admin: bool = False
    min: float | None = None
    max: float | None = None

    @property
    def domain(self) -> str:
        return self.entity.split(".", 1)[0]


@dataclass
class HomeConfig:
    rooms: list[tuple[str, list[Tile]]] = field(default_factory=list)
    dashboard_url: str = ""
    # Which app section shows each room: "home" (default) or "internet".
    room_sections: dict[str, str] = field(default_factory=dict)

    @property
    def tiles(self) -> dict[str, Tile]:
        return {t.entity: t for _, ts in self.rooms for t in ts}

    @classmethod
    def load(cls, path: Path) -> HomeConfig:
        if not path.is_file():
            return cls()
        data = tomllib.loads(path.read_text())
        rooms, sections = [], {}
        for room in data.get("rooms", []):
            tiles = []
            for t in room.get("tiles", []):
                tile = Tile(**t)
                if tile.kind not in KINDS:
                    raise ValueError(f"{tile.entity}: unknown kind {tile.kind!r}")
                if tile.kind == "toggle" and tile.domain not in TOGGLE_DOMAINS:
                    raise ValueError(f"{tile.entity}: can't toggle a {tile.domain}")
                if tile.kind == "script" and tile.domain != "script":
                    raise ValueError(f"{tile.entity}: kind script needs a script entity")
                if tile.kind == "thermostat" and (
                    tile.domain != "climate" or tile.min is None or tile.max is None
                ):
                    raise ValueError(f"{tile.entity}: thermostat needs climate + min/max")
                tiles.append(tile)
            rooms.append((room["name"], tiles))
            sections[room["name"]] = room.get("section", "home")
            if sections[room["name"]] not in SECTIONS:
                raise ValueError(f"room {room['name']}: unknown section {sections[room['name']]!r}")
        cfg = cls(rooms=rooms, dashboard_url=data.get("dashboard_url", ""))
        cfg.room_sections = sections
        return cfg


def config(request: Request) -> HomeConfig:
    st = request.app.state
    if not hasattr(st, "home_config"):
        st.home_config = HomeConfig.load(st.settings.home_config)
    return st.home_config


def visible(tile: Tile, user: User) -> bool:
    return user.is_admin or not tile.admin


class HAError(Exception):
    pass


async def ha(request: Request, method: str, path: str, json: dict | None = None):
    s = request.app.state.settings
    if not s.ha_token:
        raise HTTPException(503, "Home isn't set up yet (no Home Assistant token).")
    try:
        r = await request.app.state.http.request(
            method,
            f"{s.ha_url}{path}",
            json=json,
            timeout=10,
            headers={"Authorization": f"Bearer {s.ha_token}"},
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, "Couldn't reach Home Assistant.") from e
    if r.status_code == 401:
        raise HTTPException(502, "Home Assistant rejected the token. Check HA_TOKEN.")
    if r.status_code >= 400:
        raise HTTPException(502, f"Home Assistant answered HTTP {r.status_code}.")
    return r.json()


def _tile_state(tile: Tile, st: dict | None) -> dict:
    out = {"entity": tile.entity, "label": tile.label, "kind": tile.kind, "confirm": tile.confirm}
    if st is None:
        return out | {"state": "unknown", "available": False}
    state = st.get("state", "unknown")
    out |= {
        "state": state,
        "available": state not in ("unavailable", "unknown"),
        "on": state in ON_STATES,
    }
    if tile.kind == "thermostat":
        a = st.get("attributes", {})
        lo = max(tile.min, a.get("min_temp", tile.min))
        hi = min(tile.max, a.get("max_temp", tile.max))
        out |= {
            "current": a.get("current_temperature"),
            "target": a.get("temperature"),
            "min": lo,
            "max": hi,
            "step": a.get("target_temp_step") or 1,
            "mode": state,
            # heat_cool ("auto") mode: two targets instead of one
            "low": a.get("target_temp_low"),
            "high": a.get("target_temp_high"),
            "range": a.get("temperature") is None
            and a.get("target_temp_low") is not None
            and a.get("target_temp_high") is not None,
        }
    return out


@router.get("/status")
async def status(
    request: Request, section: str = "home", user: User = Depends(current_user)
) -> dict:
    cfg = config(request)
    s = request.app.state.settings
    if not s.ha_token:
        return {
            "built": True,
            "configured": False,
            "dashboard_url": cfg.dashboard_url,
            "message": "Home isn't set up yet: it needs a Home Assistant token (HA_TOKEN).",
        }
    states = {x["entity_id"]: x for x in await ha(request, "GET", "/api/states")}
    rooms = []
    for name, tiles in cfg.rooms:
        if cfg.room_sections.get(name, "home") != section:
            continue
        shown = [_tile_state(t, states.get(t.entity)) for t in tiles if visible(t, user)]
        if shown:
            rooms.append({"name": name, "tiles": shown})
    return {
        "built": True,
        "configured": True,
        "rooms": rooms,
        "dashboard_url": cfg.dashboard_url,
        "checked_at": int(time.time()),
    }


class ActionIn(BaseModel):
    entity: str
    action: str  # on | off | run | set_temperature
    value: float | None = None
    low: float | None = None  # heat_cool mode: heat-to and cool-to targets
    high: float | None = None
    confirmed: bool = False


@router.post("/action")
async def action(body: ActionIn, request: Request, user: User = Depends(current_user)) -> dict:
    tile = config(request).tiles.get(body.entity)
    if tile is None or not visible(tile, user):
        raise HTTPException(403, "That isn't something this app may control.")
    if tile.confirm and not body.confirmed:
        raise HTTPException(409, f"Confirm first: {tile.label}.")
    if tile.kind == "toggle" and body.action in ("on", "off"):
        domain = tile.domain
        service, data = f"turn_{body.action}", {"entity_id": tile.entity}
    elif tile.kind == "script" and body.action == "run":
        domain, service, data = "script", "turn_on", {"entity_id": tile.entity}
    elif tile.kind == "thermostat" and body.action == "set_temperature":
        between = f"Pick temperatures between {tile.min:g} and {tile.max:g}."
        domain, service = "climate", "set_temperature"
        if body.low is not None or body.high is not None:
            lo, hi = body.low, body.high
            if lo is None or hi is None or not (tile.min <= lo < hi <= tile.max):
                raise HTTPException(400, between + " Heat-to must be below cool-to.")
            data = {"entity_id": tile.entity, "target_temp_low": lo, "target_temp_high": hi}
        else:
            if body.value is None or not (tile.min <= body.value <= tile.max):
                raise HTTPException(400, between)
            data = {"entity_id": tile.entity, "temperature": body.value}
    else:
        raise HTTPException(403, "That action isn't allowed here.")
    await ha(request, "POST", f"/api/services/{domain}/{service}", data)
    request.app.state.db.execute(
        "INSERT INTO home_actions (at, user_id, entity, action, value) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), user.id, tile.entity, body.action, body.value),
    )
    return {"ok": True}
