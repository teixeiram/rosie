"""The Kitchen Hub recipe format and the signed "Send to kitchen" message.

Hub format (what recipe-relay's recipe.py parses best):

    # Recipe name
    Serves 4 · 30 minutes

    ## Ingredients
    - 2 cups flour

    ## Steps
    1. One action per step, one or two sentences.

    ## Notes
    - optional

The signature matches recipe-relay/recipe.py exactly:
    body + "\\nsig:<ts>:" + sha256hex(secret + "\\n" + ts + "\\n" + body + "\\n" + secret)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import httpx

MAX_BYTES = 60_000  # the relay accepts up to 64 KB; same margin as the kitchen app


@dataclass
class Recipe:
    title: str
    serves: str = ""
    total_time: str = ""
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _clean(s: str) -> str:
    return " ".join(str(s).split())


def render(r: Recipe) -> str:
    """Recipe -> Hub-format markdown. No text before the title or after the notes."""
    lines = [f"# {_clean(r.title) or 'Recipe'}"]
    meta = " · ".join(x for x in (_clean(r.serves), _clean(r.total_time)) if x)
    if meta:
        lines.append(meta)
    lines += ["", "## Ingredients"]
    lines += [f"- {_clean(i).lstrip('-*• ').strip()}" for i in r.ingredients if _clean(i)]
    lines += ["", "## Steps"]
    steps = [_clean(s) for s in r.steps if _clean(s)]
    lines += [f"{n}. {s}" for n, s in enumerate(steps, 1)]
    notes = [_clean(n).lstrip("-*• ").strip() for n in r.notes if _clean(n)]
    if notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in notes]
    return "\n".join(lines)


def title_of(md: str) -> str:
    """Same rule as the relay: first '# ' heading, else the first non-empty line."""
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            continue
        return s[2:].strip() if s.startswith("# ") else s.strip("*# ")
    return ""


def sign(secret: str, body: str, ts: int | None = None) -> str:
    ts_s = str(int(time.time()) if ts is None else ts)
    digest = hashlib.sha256(f"{secret}\n{ts_s}\n{body}\n{secret}".encode()).hexdigest()
    return f"{body}\nsig:{ts_s}:{digest}"


class SendError(Exception):
    pass


async def send_to_kitchen(
    client: httpx.AsyncClient, server: str, topic: str, secret: str, markdown: str
) -> None:
    body = markdown.replace("\r\n", "\n").strip()
    if not body:
        raise SendError("The recipe is empty.")
    if len(body.encode()) > MAX_BYTES:
        raise SendError("The recipe is too long for the Hub (max 58 KB).")
    if not topic or not secret:
        raise SendError("Send to kitchen is not configured on the server.")
    try:
        resp = await client.post(
            f"{server}/{topic}", content=sign(secret, body).encode(), timeout=20
        )
    except httpx.HTTPError as e:
        raise SendError(f"Couldn't reach ntfy.sh ({type(e).__name__}).") from e
    if resp.status_code >= 300:
        raise SendError(f"ntfy.sh answered HTTP {resp.status_code}.")
