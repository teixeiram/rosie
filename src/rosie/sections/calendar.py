"""Calendar: family events from Home Assistant (issue #26).

The calendars (Google, Outlook and PagerDuty, connected in Home Assistant) are
listed in home.toml under [[calendars]]; only those are read, with a label and
colour each. Everyone sees full event details (a deliberate household choice).
For the admin, calendars HA has that aren't listed yet are named, so adding one
is copy and paste. The HA token stays on the server.

The browser sends its own local midnight as `start`, so times line up with the
phone's time zone without the server knowing it.
"""

from __future__ import annotations

import asyncio
import re
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import User, current_user
from .home import ha

SECTION = {"id": "calendar", "title": "Calendar", "admin_only": False}
router = APIRouter(prefix="/api/calendar", tags=["calendar"])

MAX_DAYS = 42
CACHE_SECONDS = 60
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETTE = ("#2563eb", "#db2777", "#ea580c", "#16a34a", "#7c3aed", "#0891b2")


@dataclass(frozen=True)
class Calendar:
    entity: str
    label: str
    color: str = ""
    tag: str = ""  # short name shown on every day a multi-day event covers


def load(data: dict) -> list[Calendar]:
    """[[calendars]] from home.toml, validated. Missing colours come from a palette."""
    out = []
    for i, raw in enumerate(data.get("calendars", [])):
        c = Calendar(**raw)
        if not c.entity.startswith("calendar.") or not c.label.strip():
            raise ValueError(f"{c.entity}: needs a calendar.* entity and a label")
        if c.color and not COLOR.match(c.color):
            raise ValueError(f"{c.entity}: color must look like #2563eb")
        if len(c.tag.strip()) > 24:
            raise ValueError(f"{c.entity}: tag must be 24 characters or fewer")
        color = c.color or PALETTE[i % len(PALETTE)]
        out.append(Calendar(c.entity, c.label.strip(), color, c.tag.strip()))
    return out


def calendars(request: Request) -> list[Calendar]:
    st = request.app.state
    if not hasattr(st, "calendars"):
        path = st.settings.home_config
        st.calendars = load(tomllib.loads(path.read_text())) if path.is_file() else []
    return st.calendars


def _when(v: dict) -> tuple[str, bool]:
    """HA gives {"dateTime": ...} for timed events and {"date": ...} for all-day ones."""
    if v.get("dateTime"):
        return v["dateTime"], False
    return v.get("date", ""), True


def _event(cal: Calendar, e: dict) -> dict | None:
    start, all_day = _when(e.get("start") or {})
    end, _ = _when(e.get("end") or {})
    if not start:
        return None
    return {
        "calendar": cal.label,
        "color": cal.color,
        "title": (e.get("summary") or "(no title)").strip()[:200],
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": (e.get("location") or "").strip()[:200],
        "tag": cal.tag,
    }


def _sort_key(ev: dict) -> tuple:
    # All-day events first on their day, then by start time.
    day = ev["start"][:10]
    return (day, not ev["all_day"], ev["start"] if not ev["all_day"] else "", ev["title"])


@router.get("")
async def events(
    request: Request, start: str = "", days: int = 7, user: User = Depends(current_user)
) -> dict:
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(400, f"days must be 1 to {MAX_DAYS}.")
    try:
        t0 = datetime.fromisoformat(start) if start else datetime.now().astimezone()
    except ValueError as e:
        raise HTTPException(400, "start must be an ISO date-time with a time zone.") from e
    if t0.tzinfo is None:
        raise HTTPException(400, "start needs a time zone offset.")
    t1 = t0 + timedelta(days=days)
    cals = calendars(request)
    s = request.app.state.settings
    out = {
        "configured": bool(s.ha_token),
        "calendars": [{"label": c.label, "color": c.color} for c in cals],
        "events": [],
        "errors": [],
    }
    if not s.ha_token:
        return out

    cache = request.app.state.__dict__.setdefault("calendar_cache", {})
    key = (t0.isoformat(), days)
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        found = hit[1]
    else:
        q = urlencode({"start": t0.isoformat(), "end": t1.isoformat()})

        async def one(c: Calendar):
            return await ha(request, "GET", f"/api/calendars/{c.entity}?{q}")

        found = await asyncio.gather(*(one(c) for c in cals), return_exceptions=True)
        if not any(isinstance(r, BaseException) for r in found):
            cache.clear()  # one window at a time is plenty
            cache[key] = (time.monotonic(), found)

    for c, r in zip(cals, found, strict=True):
        if isinstance(r, BaseException):
            out["errors"].append(f"{c.label}: couldn't be read from Home Assistant.")
            continue
        out["events"] += [ev for e in r if isinstance(e, dict) and (ev := _event(c, e))]
    out["events"].sort(key=_sort_key)

    if user.is_admin:
        try:
            states = await ha(request, "GET", "/api/states")
            listed = {c.entity for c in cals}
            out["unlisted"] = [
                {"entity": e, "name": st.get("attributes", {}).get("friendly_name", "")}
                for st in states
                if (e := st.get("entity_id", "")).startswith("calendar.") and e not in listed
            ]
        except HTTPException:
            out["unlisted"] = []
    return out
