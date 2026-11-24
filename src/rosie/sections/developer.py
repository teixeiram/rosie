"""Developer: browse and discuss Forgejo issues, file new ones (issue #10).

Admin only, enforced on every route. Only repos in FORGEJO_REPOS are reachable.
The Forgejo token (issue scope only) stays on the server. Creating issues or
comments needs FORGEJO_WRITE=1 *and* an explicit confirmation in each request;
everything posted is logged with who did it.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import usage
from ..auth import User, require_admin
from ..dev_chat import dev_chat, issue_context, list_context
from ..recipe_chat import ChatError

SECTION = {"id": "developer", "title": "Developer", "admin_only": True}
router = APIRouter(prefix="/api/developer", tags=["developer"])

STATES = {"open", "closed", "all"}


def _settings(request: Request):
    return request.app.state.settings


def _repo(request: Request, repo: str) -> str:
    if repo not in _settings(request).forgejo_repos:
        raise HTTPException(403, "That repository isn't one this app may use.")
    return repo


async def forgejo(request: Request, method: str, path: str, *, params=None, json=None):
    s = _settings(request)
    if not s.forgejo_token:
        raise HTTPException(503, "Developer isn't set up yet (no FORGEJO_TOKEN).")
    try:
        r = await request.app.state.http.request(
            method,
            f"{s.forgejo_url}/api/v1{path}",
            params=params,
            json=json,
            timeout=15,
            headers={"Authorization": f"token {s.forgejo_token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, "Couldn't reach Forgejo.") from e
    if r.status_code == 401:
        raise HTTPException(502, "Forgejo rejected the token. Check FORGEJO_TOKEN.")
    if r.status_code == 403:
        raise HTTPException(502, "The Forgejo token isn't allowed to do that.")
    if r.status_code == 404:
        raise HTTPException(404, "Forgejo couldn't find that.")
    if r.status_code >= 400:
        raise HTTPException(502, f"Forgejo answered HTTP {r.status_code}.")
    return r.json()


def _slim(repo: str, i: dict, web: str) -> dict:
    return {
        "repo": repo,
        "number": i["number"],
        "title": i["title"],
        "state": i["state"],
        "labels": [lb["name"] for lb in i.get("labels") or []],
        "comments": i.get("comments", 0),
        "updated_at": i.get("updated_at", ""),
        "url": f"{web}/{repo}/issues/{i['number']}",
    }


@router.get("/status")
def status(request: Request, user: User = Depends(require_admin)) -> dict:
    s = _settings(request)
    return {
        "built": True,
        "configured": bool(s.forgejo_token),
        "write_enabled": s.forgejo_write,
        "repos": list(s.forgejo_repos),
        "ai": bool(s.anthropic_api_key),
        "message": "" if s.forgejo_token else "Developer isn't set up yet: it needs FORGEJO_TOKEN.",
    }


async def _list(request: Request, repo: str, state: str, q: str = "") -> list[dict]:
    params = {"type": "issues", "state": state, "limit": 50, "page": 1}
    if q:
        params["q"] = q[:100]
    return await forgejo(request, "GET", f"/repos/{repo}/issues", params=params)


@router.get("/issues")
async def list_issues(
    request: Request,
    repo: str,
    state: str = "open",
    q: str = "",
    user: User = Depends(require_admin),
) -> dict:
    repo = _repo(request, repo)
    if state not in STATES:
        raise HTTPException(400, "state must be open, closed or all.")
    web = _settings(request).forgejo_web_url
    return {"issues": [_slim(repo, i, web) for i in await _list(request, repo, state, q)]}


async def _issue(request: Request, repo: str, number: int) -> tuple[dict, list[dict]]:
    issue = await forgejo(request, "GET", f"/repos/{repo}/issues/{number}")
    if issue.get("pull_request") is not None:
        raise HTTPException(404, "That number is a pull request, not an issue.")
    comments = await forgejo(request, "GET", f"/repos/{repo}/issues/{number}/comments")
    return issue, comments


@router.get("/issue")
async def get_issue(
    request: Request, repo: str, number: int, user: User = Depends(require_admin)
) -> dict:
    repo = _repo(request, repo)
    issue, comments = await _issue(request, repo, number)
    web = _settings(request).forgejo_web_url
    return _slim(repo, issue, web) | {
        "body": issue.get("body") or "",
        "user": issue.get("user", {}).get("login", ""),
        "created_at": issue.get("created_at", ""),
        "thread": [
            {
                "user": c.get("user", {}).get("login", ""),
                "created_at": c.get("created_at", ""),
                "body": c.get("body") or "",
            }
            for c in comments
        ],
    }


class ChatMessage(BaseModel):
    role: str = Field(max_length=16)
    content: str = Field(max_length=30_000)


class ChatIn(BaseModel):
    repo: str = Field(max_length=200)
    number: int | None = None
    state: str = "open"
    messages: list[ChatMessage] = Field(max_length=60)


@router.post("/chat")
async def chat(body: ChatIn, request: Request, user: User = Depends(require_admin)) -> dict:
    s = _settings(request)
    db = request.app.state.db
    repo = _repo(request, body.repo)
    if not s.anthropic_api_key:
        raise HTTPException(503, "Chat is not set up yet (no Anthropic API key).")
    if usage.spent_this_month(db) >= s.monthly_cap_usd:
        raise HTTPException(
            429,
            f"This month's AI budget (${s.monthly_cap_usd:.2f}) is used up. "
            "Raise ROSIE_MONTHLY_CAP_USD to continue.",
        )
    if body.number is not None:
        issue, comments = await _issue(request, repo, body.number)
        context = issue_context(repo, issue, comments)
    else:
        state = body.state if body.state in STATES else "open"
        context = list_context(repo, state, await _list(request, repo, state))

    def on_usage(model, tin, tout, cwrite, cread, request_id):
        usage.record(
            db,
            user_id=user.id,
            feature="developer.chat",
            model=model,
            input_tokens=tin,
            output_tokens=tout,
            request_id=request_id,
            cache_write_tokens=cwrite,
            cache_read_tokens=cread,
        )

    try:
        out = await dev_chat(
            request.app.state.anthropic,
            s.chat_model,
            context,
            [m.model_dump() for m in body.messages],
            on_usage=on_usage,
        )
    except ChatError as e:
        raise HTTPException(422, str(e)) from e
    return out | {"month_spent_usd": round(usage.spent_this_month(db), 2)}


class NewIssueIn(BaseModel):
    repo: str = Field(max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=60_000)
    confirmed: bool = False


class CommentIn(BaseModel):
    repo: str = Field(max_length=200)
    number: int
    body: str = Field(min_length=1, max_length=60_000)
    confirmed: bool = False


def _may_write(request: Request, confirmed: bool) -> None:
    if not _settings(request).forgejo_write:
        raise HTTPException(403, "Writing to Forgejo is switched off (FORGEJO_WRITE).")
    if not confirmed:
        raise HTTPException(409, "Confirm first.")


def _log(request: Request, user: User, action: str, repo: str, number: int) -> None:
    request.app.state.db.execute(
        "INSERT INTO home_actions (at, user_id, entity, action, value) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), user.id, f"forgejo:{repo}#{number}", action, None),
    )


@router.post("/issues")
async def create_issue(body: NewIssueIn, request: Request, user: User = Depends(require_admin)):
    repo = _repo(request, body.repo)
    _may_write(request, body.confirmed)
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(400, "The issue needs a title.")
    i = await forgejo(
        request, "POST", f"/repos/{repo}/issues", json={"title": title, "body": body.body}
    )
    _log(request, user, "create_issue", repo, i["number"])
    return _slim(repo, i, _settings(request).forgejo_web_url)


@router.post("/comments")
async def create_comment(body: CommentIn, request: Request, user: User = Depends(require_admin)):
    repo = _repo(request, body.repo)
    _may_write(request, body.confirmed)
    await forgejo(
        request, "POST", f"/repos/{repo}/issues/{body.number}/comments", json={"body": body.body}
    )
    _log(request, user, "comment", repo, body.number)
    web = _settings(request).forgejo_web_url
    return {"ok": True, "url": f"{web}/{quote(repo)}/issues/{body.number}"}
