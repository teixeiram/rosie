"""Sections that exist in the menu but are not built yet (Media).

Each gets its own router so the role rules are already in place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import User, current_user

media = APIRouter(prefix="/api/media", tags=["media"])

MEDIA = {"id": "media", "title": "Media", "admin_only": False}


@media.get("/status")
def media_status(user: User = Depends(current_user)) -> dict:
    return {
        "built": False,
        "message": "Coming later: Shield + Emby button (#11), "
        "record-room WiiM (#12), what record is playing (#13).",
    }
