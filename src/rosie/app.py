"""FastAPI app: login, section routing, PWA shell and security headers.

Each section is loaded on its own; if one fails to import, the app still starts
and the menu shows that section as broken (the rest keep working).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, links, usage
from .config import Settings

log = logging.getLogger("rosie")
STATIC = Path(__file__).parent / "static"

# (module, router attribute, section attribute)
SECTIONS = [
    ("rosie.sections.today", "router", "SECTION"),
    ("rosie.sections.calendar", "router", "SECTION"),
    ("rosie.sections.kitchen", "router", "SECTION"),
    ("rosie.sections.lists", "router", "SECTION"),
    ("rosie.sections.placeholders", "media", "MEDIA"),
    ("rosie.sections.internet", "router", "SECTION"),
    ("rosie.sections.home", "router", "SECTION"),
    ("rosie.sections.developer", "router", "SECTION"),
]

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http = app.state.http = httpx.AsyncClient(timeout=15, trust_env=False)
        claude = app.state.anthropic = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or "unset", timeout=120, max_retries=2
        )
        yield
        await http.aclose()
        await claude.close()

    app = FastAPI(
        title="home app", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.db = db.connect(settings.db_path)
    app.state.sections = []
    try:
        app.state.links = links.load(settings.links_config)
    except Exception:  # a bad links.toml must not take the app down
        log.exception("links.toml failed to load; the menu shows no links")
        app.state.links = []

    for mod_name, router_attr, section_attr in SECTIONS:
        try:
            mod = importlib.import_module(mod_name)
            app.include_router(getattr(mod, router_attr))
            app.state.sections.append({**getattr(mod, section_attr), "ok": True})
        except Exception:
            log.exception("section %s failed to load", mod_name)
            sid = section_attr.lower() if section_attr != "SECTION" else mod_name.rsplit(".", 1)[1]
            app.state.sections.append(
                {"id": sid, "title": sid.title(), "admin_only": sid == "developer", "ok": False}
            )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # CSRF: state-changing API calls must come from our own script (custom
        # header + same origin). The session cookie is also SameSite=Strict.
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if request.headers.get("x-requested-with") != "fetch" or (
                origin and origin.split("://", 1)[-1] != host
            ):
                return JSONResponse({"detail": "Request blocked"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s", request.url.path)
            response = JSONResponse(
                {"detail": "Something went wrong in this section."}, status_code=500
            )
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.startswith("/static/"):
            # Without this, phones guess a lifetime and keep running an old
            # app.js after a deploy. Revalidating is a cheap 304.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # --- auth -------------------------------------------------------------

    @app.post("/api/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict:
        conn = request.app.state.db
        try:
            user = auth.authenticate(conn, body.username.strip(), body.password)
        except auth.LoginLocked:
            raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes.") from None
        if user is None:
            raise HTTPException(401, "Wrong username or password.")
        token = auth.new_session(conn, user, settings.session_days)
        response.set_cookie(
            auth.COOKIE,
            token,
            max_age=settings.session_days * 86400,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        return {"user": user.public()}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        token = request.cookies.get(auth.COOKIE)
        if token:
            auth.end_session(request.app.state.db, token)
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, user: auth.User = Depends(auth.current_user)) -> dict:
        sections = [
            {k: s[k] for k in ("id", "title", "ok")}
            for s in request.app.state.sections
            if user.is_admin or not s["admin_only"]
        ]
        tabs = [
            s
            for s in sections
            if next(x for x in request.app.state.sections if x["id"] == s["id"]).get("tab", True)
        ]
        hidden = {s["id"]: s["ok"] for s in sections if s not in tabs}
        return {
            "user": user.public(),
            "sections": tabs,
            "parts": hidden,
            "links": links.for_user(request.app.state.links, user.is_admin),
        }

    @app.get("/api/app")
    def app_info() -> dict:
        b = settings.branding
        return {"name": b.name, "short_name": b.short_name, "description": b.description}

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        request.app.state.db.execute("SELECT 1")
        return {"ok": True}

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        """Prometheus gauges for AI spending only (no personal data), so
        alert rules can warn when the month's cap is near or spending spikes."""
        conn = request.app.state.db
        total, calls = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM api_usage"
        ).fetchone()
        lines = [
            "# HELP rosie_ai_spend_month_usd Estimated Anthropic spend this calendar month (UTC).",
            "# TYPE rosie_ai_spend_month_usd gauge",
            f"rosie_ai_spend_month_usd {usage.spent_this_month(conn):.6f}",
            "# HELP rosie_ai_cap_usd Monthly AI spending cap.",
            "# TYPE rosie_ai_cap_usd gauge",
            f"rosie_ai_cap_usd {settings.monthly_cap_usd:.2f}",
            "# HELP rosie_ai_spend_usd_total Estimated Anthropic spend since install.",
            "# TYPE rosie_ai_spend_usd_total counter",
            f"rosie_ai_spend_usd_total {float(total):.6f}",
            "# HELP rosie_ai_requests_total Billed Anthropic API responses since install.",
            "# TYPE rosie_ai_requests_total counter",
            f"rosie_ai_requests_total {int(calls)}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # --- PWA shell --------------------------------------------------------

    @app.get("/manifest.webmanifest")
    def manifest() -> Response:
        b = settings.branding
        data = {
            "name": b.name,
            "short_name": b.short_name,
            "description": b.description,
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": b.background_color,
            "theme_color": b.theme_color,
            "icons": [
                {
                    "src": f"/static/icons/{b.icon_192}",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": f"/static/icons/{b.icon_512}",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
            # Android: share a recipe page or text straight into the Kitchen import.
            "share_target": {
                "action": "/",
                "method": "GET",
                "params": {"title": "title", "text": "text", "url": "url"},
            },
        }
        return Response(
            json.dumps(data),
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    index_html = (STATIC / "index.html").read_text()
    # The page names the exact script and stylesheet versions, so a deploy is
    # a new URL that no cache can answer with the old file.
    versions = {
        k: hashlib.sha256((STATIC / f).read_bytes()).hexdigest()[:12]
        for k, f in (("v_js", "app.js"), ("v_css", "style.css"))
    }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        b = settings.branding
        html = (
            index_html.replace("{{name}}", b.name)
            .replace("{{theme_color}}", b.theme_color)
            .replace("{{icon_192}}", b.icon_192)
            .replace("{{v_js}}", versions["v_js"])
            .replace("{{v_css}}", versions["v_css"])
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
