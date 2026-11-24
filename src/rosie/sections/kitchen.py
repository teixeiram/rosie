"""Kitchen: import a recipe from a link or pasted text, send it to the Hub, save it.

Issue #3. Shared lists (#5), recipe chat (#8) and Egg Genius (#9) come later.
"""

from __future__ import annotations

import base64
import binascii
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import usage
from ..auth import User, current_user
from ..htmltext import extract
from ..hubformat import MAX_BYTES, SendError, render, send_to_kitchen, title_of
from ..netfetch import FetchError, fetch_page
from ..recipe_ai import PDF_TYPE, ExtractError, build_file_input, build_input, extract_recipe
from ..recipe_chat import ChatError, chat

SECTION = {"id": "kitchen", "title": "Kitchen", "admin_only": False}
router = APIRouter(prefix="/api/kitchen", tags=["kitchen"])


class ImportIn(BaseModel):
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=200_000)


class FileIn(BaseModel):
    media_type: str = Field(max_length=40)
    data: str = Field(max_length=28_000_000)  # base64


class FilesIn(BaseModel):
    files: list[FileIn] = Field(min_length=1, max_length=10)
    note: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=2000)


# What a file must start with to be what it says it is (the browser's type isn't trusted).
_MAGIC = {
    "image/jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
    "image/png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    "image/gif": lambda b: b[:4] == b"GIF8",
    "image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    PDF_TYPE: lambda b: b[:5] == b"%PDF-",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Anthropic's per-image limit
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024  # keeps the request under the API's 32 MB


def check_files(files: list[FileIn]) -> list[tuple[str, str]]:
    out, total, pdfs = [], 0, 0
    for i, f in enumerate(files, 1):
        check = _MAGIC.get(f.media_type)
        if check is None:
            raise HTTPException(
                400, f"File {i}: only photos/screenshots (JPEG, PNG, WebP, GIF) or a PDF."
            )
        try:
            raw = base64.b64decode(f.data, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, f"File {i} didn't upload properly. Try again.") from e
        if not check(raw):
            raise HTTPException(
                400, f"File {i} isn't really a {f.media_type.split('/')[1].upper()}."
            )
        if f.media_type == PDF_TYPE:
            pdfs += 1
            if len(raw) > MAX_PDF_BYTES:
                raise HTTPException(413, "That PDF is too big (over 20 MB).")
        elif len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Image {i} is too big (over 5 MB).")
        total += len(raw)
        out.append((f.media_type, f.data))
    if pdfs > 1:
        raise HTTPException(400, "One PDF at a time, please.")
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(413, "That's too much at once. Try fewer images.")
    return out


class SendIn(BaseModel):
    markdown: str = Field(max_length=MAX_BYTES * 2)


class ChatMessage(BaseModel):
    role: str = Field(max_length=16)
    content: str = Field(max_length=30_000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(max_length=60)
    chat_id: int | None = None  # carry on a saved chat; None starts a new one


class SaveIn(BaseModel):
    markdown: str = Field(max_length=MAX_BYTES * 2)
    source_url: str = Field(default="", max_length=2000)


@router.get("/status")
def status(request: Request, user: User = Depends(current_user)) -> dict:
    s = request.app.state.settings
    db = request.app.state.db
    return {
        "import_configured": bool(s.anthropic_api_key),
        "send_configured": bool(s.recipe_topic and s.recipe_secret),
        "month_spent_usd": round(usage.spent_this_month(db), 2),
        "monthly_cap_usd": s.monthly_cap_usd,
    }


@router.post("/import")
async def import_recipe(body: ImportIn, request: Request, user: User = Depends(current_user)):
    s = request.app.state.settings
    db = request.app.state.db
    url, text = body.url.strip(), body.text.strip()
    if not url and not text:
        raise HTTPException(400, "Paste a link or the recipe text.")
    if not s.anthropic_api_key:
        raise HTTPException(503, "Recipe import is not set up yet (no Anthropic API key).")
    spent = usage.spent_this_month(db)
    if spent >= s.monthly_cap_usd:
        raise HTTPException(
            429,
            f"This month's AI budget (${s.monthly_cap_usd:.2f}) is used up. "
            "Raise ROSIE_MONTHLY_CAP_USD to continue.",
        )

    if url and not text:
        try:
            page = await fetch_page(url)
        except FetchError as e:
            raise HTTPException(422, str(e)) from e
        ld, page_title, page_text = extract(page.text)
        if not ld and len(page_text) < 200:
            raise HTTPException(
                422,
                "That page has almost no text (it may need JavaScript). "
                "Paste the recipe text instead.",
            )
        content, truncated = build_input(url=page.url, page_title=page_title, ld=ld, text=page_text)
        source = page.url
    else:
        content, truncated = build_input(url=url, text=text)
        source = url

    def on_usage(model: str, tin: int, tout: int, request_id: str | None) -> None:
        usage.record(
            db,
            user_id=user.id,
            feature="kitchen.import",
            model=model,
            input_tokens=tin,
            output_tokens=tout,
            request_id=request_id,
        )

    try:
        result = await extract_recipe(
            request.app.state.anthropic, s.model, content, truncated, on_usage=on_usage
        )
    except ExtractError as e:
        raise HTTPException(422, str(e)) from e
    md = render(result.recipe)
    return {
        "markdown": md,
        "title": title_of(md),
        "source_url": source,
        "truncated": result.truncated,
        "cost_usd": round(
            usage.cost_usd(result.model, result.input_tokens, result.output_tokens), 4
        ),
    }


@router.post("/import-files")
async def import_files(body: FilesIn, request: Request, user: User = Depends(current_user)):
    """A recipe from screenshots, photos or a PDF (#22): for sites that block us,
    Instagram posts, cookbook pages."""
    s = request.app.state.settings
    db = request.app.state.db
    files = check_files(body.files)
    if not s.anthropic_api_key:
        raise HTTPException(503, "Recipe import is not set up yet (no Anthropic API key).")
    if usage.spent_this_month(db) >= s.monthly_cap_usd:
        raise HTTPException(
            429,
            f"This month's AI budget (${s.monthly_cap_usd:.2f}) is used up. "
            "Raise ROSIE_MONTHLY_CAP_USD to continue.",
        )

    def on_usage(model: str, tin: int, tout: int, request_id: str | None) -> None:
        usage.record(
            db,
            user_id=user.id,
            feature="kitchen.import_files",
            model=model,
            input_tokens=tin,
            output_tokens=tout,
            request_id=request_id,
        )

    try:
        result = await extract_recipe(
            request.app.state.anthropic,
            s.model,
            build_file_input(files, body.note.strip()),
            False,
            on_usage=on_usage,
        )
    except ExtractError as e:
        raise HTTPException(422, str(e)) from e
    md = render(result.recipe)
    return {
        "markdown": md,
        "title": title_of(md),
        "source_url": body.source_url.strip(),
        "truncated": False,
        "cost_usd": round(
            usage.cost_usd(result.model, result.input_tokens, result.output_tokens), 4
        ),
    }


@router.post("/send")
async def send(body: SendIn, request: Request, user: User = Depends(current_user)) -> dict:
    s = request.app.state.settings
    try:
        await send_to_kitchen(
            request.app.state.http, s.ntfy_server, s.recipe_topic, s.recipe_secret, body.markdown
        )
    except SendError as e:
        raise HTTPException(502, str(e)) from e
    return {"sent": True, "title": title_of(body.markdown)}


@router.post("/chat")
async def recipe_chat(body: ChatIn, request: Request, user: User = Depends(current_user)):
    s = request.app.state.settings
    db = request.app.state.db
    if not s.anthropic_api_key:
        raise HTTPException(503, "Recipe chat is not set up yet (no Anthropic API key).")
    if usage.spent_this_month(db) >= s.monthly_cap_usd:
        raise HTTPException(
            429,
            f"This month's AI budget (${s.monthly_cap_usd:.2f}) is used up. "
            "Raise ROSIE_MONTHLY_CAP_USD to continue.",
        )

    if body.chat_id is not None:
        _own_chat(db, body.chat_id, user)  # 404 before paying for a model call

    def on_usage(model, tin, tout, cwrite, cread, request_id):
        usage.record(
            db,
            user_id=user.id,
            feature="kitchen.chat",
            model=model,
            input_tokens=tin,
            output_tokens=tout,
            request_id=request_id,
            cache_write_tokens=cwrite,
            cache_read_tokens=cread,
        )

    try:
        turn = await chat(
            request.app.state.anthropic,
            s.chat_model,
            db,
            [m.model_dump() for m in body.messages],
            on_usage=on_usage,
        )
    except ChatError as e:
        raise HTTPException(422, str(e)) from e
    chat_id = _store_turn(db, user, body.chat_id, [m.model_dump() for m in body.messages], turn)
    return {
        "chat_id": chat_id,
        "reply": turn.reply,
        "saved": turn.saved,
        "recipes": turn.recipes,
        "history_text": turn.history_text,
        "month_spent_usd": round(usage.spent_this_month(db), 2),
    }


@router.post("/recipes")
def save(body: SaveIn, request: Request, user: User = Depends(current_user)) -> dict:
    md = body.markdown.replace("\r\n", "\n").strip()
    if not md:
        raise HTTPException(400, "The recipe is empty.")
    title = title_of(md)[:200] or "Recipe"
    cur = request.app.state.db.execute(
        "INSERT INTO recipes (title, markdown, source_url, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, md, body.source_url.strip() or None, user.id, int(time.time())),
    )
    return {"id": cur.lastrowid, "title": title}


def meta_of(md: str) -> str:
    """The "Serves 4 · 30 minutes" line under the title, if there is one."""
    lines = [x.strip() for x in md.split("\n") if x.strip()]
    if len(lines) > 1 and lines[0].startswith("# ") and not lines[1].startswith("#"):
        return lines[1][:200]
    return ""


@router.get("/recipes")
def list_recipes(request: Request, q: str = "", user: User = Depends(current_user)) -> dict:
    """All saved recipes, newest first. q searches titles and recipe text
    (every word must appear); title matches are listed first."""
    sql = (
        "SELECT r.id, r.title, r.markdown, r.source_url, r.created_at, u.display_name AS by"
        " FROM recipes r LEFT JOIN users u ON u.id = r.created_by"
    )
    words = q.lower().split()[:8]
    args: list = []
    if words:
        match = "instr(lower(r.title || ' ' || r.markdown), ?) > 0"
        sql += " WHERE " + " AND ".join(match for _ in words)
        args = words
    rows = request.app.state.db.execute(sql + " ORDER BY r.created_at DESC", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        md = d.pop("markdown")
        d["meta"] = meta_of(md)
        d["title_hit"] = all(w in d["title"].lower() for w in words)
        out.append(d)
    if words:
        out.sort(key=lambda d: not d["title_hit"])
    return {"recipes": out}


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: int, request: Request, user: User = Depends(current_user)) -> dict:
    row = request.app.state.db.execute(
        "SELECT id, title, markdown, source_url, created_at FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "No such recipe.")
    return dict(row)


# --- Saved chats (#23): private to each user --------------------------------

CHAT_KIND = "kitchen"


def _own_chat(db, chat_id: int, user: User):
    row = db.execute(
        "SELECT * FROM chats WHERE id = ? AND user_id = ? AND kind = ?",
        (chat_id, user.id, CHAT_KIND),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "No such chat.")
    return row


def _title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= 60 else t[:57].rstrip() + "…"


def _store_turn(db, user: User, chat_id: int | None, msgs: list[dict], turn) -> int:
    now = int(time.time())
    question = msgs[-1]["content"]
    shown = [{"role": "user", "text": question}]
    if turn.reply:
        shown.append({"role": "assistant", "text": turn.reply})
    shown += [{"card": "saved", "title": r["title"], "markdown": r["markdown"]} for r in turn.saved]
    shown += [
        {"card": "recipe", "title": r["title"], "markdown": r["markdown"]} for r in turn.recipes
    ]
    history = msgs + [{"role": "assistant", "content": turn.history_text}]
    if chat_id is None:
        cur = db.execute(
            "INSERT INTO chats (user_id, kind, title, messages, display, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user.id,
                CHAT_KIND,
                _title(question),
                json.dumps(history),
                json.dumps(shown),
                now,
                now,
            ),
        )
        return cur.lastrowid
    row = _own_chat(db, chat_id, user)
    display = json.loads(row["display"]) + shown
    db.execute(
        "UPDATE chats SET messages = ?, display = ?, updated_at = ? WHERE id = ?",
        (json.dumps(history), json.dumps(display), now, chat_id),
    )
    return chat_id


@router.get("/chats")
def list_chats(request: Request, user: User = Depends(current_user)) -> dict:
    rows = request.app.state.db.execute(
        "SELECT id, title, created_at, updated_at, messages FROM chats"
        " WHERE user_id = ? AND kind = ? ORDER BY updated_at DESC LIMIT 100",
        (user.id, CHAT_KIND),
    ).fetchall()
    return {
        "chats": [
            {
                "id": r["id"],
                "title": r["title"],
                "updated_at": r["updated_at"],
                "questions": sum(1 for m in json.loads(r["messages"]) if m["role"] == "user"),
            }
            for r in rows
        ]
    }


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, request: Request, user: User = Depends(current_user)) -> dict:
    row = _own_chat(request.app.state.db, chat_id, user)
    return {
        "id": row["id"],
        "title": row["title"],
        "updated_at": row["updated_at"],
        "messages": json.loads(row["messages"]),
        "display": json.loads(row["display"]),
    }


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: int, request: Request, user: User = Depends(current_user)) -> dict:
    _own_chat(request.app.state.db, chat_id, user)
    request.app.state.db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return {"ok": True}
