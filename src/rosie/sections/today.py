"""Today: the overview page (issue #25).

One request gives the first screen: open To-do items and how many things are
on each shopping list. Calendars join once they're in Home Assistant (#26).
Ticking an item uses the lists API, so live sync keeps working.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import User, current_user

SECTION = {"id": "today", "title": "Today", "admin_only": False}
router = APIRouter(prefix="/api/today", tags=["today"])

TODO_SHOWN = 8


@router.get("")
def today(request: Request, user: User = Depends(current_user)) -> dict:
    db = request.app.state.db
    lists = db.execute(
        "SELECT l.id, l.name, l.kind,"
        " (SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id AND i.checked = 0) AS open"
        " FROM lists l ORDER BY l.position"
    ).fetchall()
    todo = next((r for r in lists if r["kind"] == "todo"), None)
    items = []
    if todo is not None:
        items = [
            dict(r)
            for r in db.execute(
                "SELECT i.id, i.text, u.display_name AS by FROM list_items i"
                " LEFT JOIN users u ON u.id = i.added_by"
                " WHERE i.list_id = ? AND i.checked = 0 ORDER BY i.added_at LIMIT ?",
                (todo["id"], TODO_SHOWN),
            )
        ]
    return {
        "name": user.display_name,
        "todo": {
            "list_id": todo["id"] if todo else None,
            "open": todo["open"] if todo else 0,
            "items": items,
        },
        "shopping": [
            {"id": r["id"], "name": r["name"], "open": r["open"]}
            for r in lists
            if r["kind"] == "store"
        ],
    }
