"""Links for the app menu, from links.toml (issue #24)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Link:
    label: str
    url: str
    admin: bool = False


def load(path: Path) -> list[Link]:
    if not path.is_file():
        return []
    out = []
    for raw in tomllib.loads(path.read_text()).get("links", []):
        link = Link(**raw)
        if urlsplit(link.url).scheme not in ("http", "https") or not urlsplit(link.url).netloc:
            raise ValueError(f"{link.label}: only http(s) links are allowed")
        if not link.label.strip():
            raise ValueError(f"{link.url}: link needs a label")
        out.append(link)
    return out


def for_user(links: list[Link], is_admin: bool) -> list[dict]:
    return [{"label": x.label, "url": x.url} for x in links if is_admin or not x.admin]
