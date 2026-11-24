"""Settings from the environment (.env) and branding.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_DEFAULT_BRANDING = _REPO / "branding.toml"
_DEFAULT_HOME = _REPO / "home.toml"
_DEFAULT_LINKS = _REPO / "links.toml"


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Branding:
    name: str = "Rosie"
    short_name: str = "Rosie"
    description: str = ""
    hostname: str = ""
    theme_color: str = "#1f3b2d"
    background_color: str = "#1f3b2d"
    icon_192: str = "icon-192.png"
    icon_512: str = "icon-512.png"
    connector_name: str = "rosie"
    wake_word: str = "Rosie"

    @classmethod
    def load(cls, path: Path) -> Branding:
        if not path.is_file():
            return cls()
        data = tomllib.loads(path.read_text())
        known = {k: str(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    branding: Branding
    cookie_secure: bool = True
    session_days: int = 180
    # Anthropic (website import). No key = feature shows "not configured".
    anthropic_api_key: str = field(default="", repr=False)
    model: str = "claude-opus-5-5"  # recipe import (website, PDF)
    chat_model: str = "claude-sonnet-5-5"  # recipe chat: big cached context, many turns
    monthly_cap_usd: float = 20.0
    # Send to kitchen: same topic/secret as recipe-relay.
    recipe_topic: str = field(default="", repr=False)
    recipe_secret: str = field(default="", repr=False)
    ntfy_server: str = "https://ntfy.sh"
    # Internet status.
    prometheus_url: str = "http://host.docker.internal:9090"
    grafana_url: str = "http://localhost:3001"
    # Home (Home Assistant). No token = the section says it isn't set up.
    ha_url: str = "http://host.docker.internal:8123"
    ha_token: str = field(default="", repr=False)
    home_config: Path = _DEFAULT_HOME
    links_config: Path = _DEFAULT_LINKS
    # Developer (Forgejo issues, admin only). Token scope: issues only.
    forgejo_url: str = "http://host.docker.internal:3000"
    forgejo_web_url: str = "http://localhost:3000"
    forgejo_token: str = field(default="", repr=False)
    forgejo_repos: tuple[str, ...] = ()
    forgejo_write: bool = False  # creating issues / comments: off unless FORGEJO_WRITE=1

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        branding_path = Path(e.get("ROSIE_BRANDING") or _DEFAULT_BRANDING)
        return cls(
            db_path=Path(e.get("ROSIE_DB_PATH", "./data/rosie.db")),
            branding=Branding.load(branding_path),
            cookie_secure=_bool(e.get("ROSIE_COOKIE_SECURE"), True),
            session_days=int(e.get("ROSIE_SESSION_DAYS", "180")),
            anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
            model=e.get("ROSIE_MODEL", "claude-opus-5-5"),
            chat_model=e.get("ROSIE_CHAT_MODEL", "claude-sonnet-5-5"),
            monthly_cap_usd=float(e.get("ROSIE_MONTHLY_CAP_USD", "20")),
            recipe_topic=e.get("RECIPE_TOPIC", ""),
            recipe_secret=e.get("RECIPE_SECRET", ""),
            ntfy_server=e.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
            prometheus_url=e.get("PROMETHEUS_URL", "http://host.docker.internal:9090").rstrip("/"),
            grafana_url=e.get("GRAFANA_URL", "http://localhost:3001").rstrip("/"),
            ha_url=e.get("HA_URL", "http://host.docker.internal:8123").rstrip("/"),
            ha_token=e.get("HA_TOKEN", ""),
            home_config=Path(e.get("ROSIE_HOME_CONFIG") or _DEFAULT_HOME),
            links_config=Path(e.get("ROSIE_LINKS") or _DEFAULT_LINKS),
            forgejo_url=e.get("FORGEJO_URL", "http://host.docker.internal:3000").rstrip("/"),
            forgejo_web_url=e.get("FORGEJO_WEB_URL", "http://localhost:3000").rstrip("/"),
            forgejo_token=e.get("FORGEJO_TOKEN", ""),
            forgejo_repos=tuple(
                r.strip()
                for r in e.get("FORGEJO_REPOS", "").split(",")
                if r.strip().count("/") == 1
            ),
            forgejo_write=_bool(e.get("FORGEJO_WRITE"), False),
        )
