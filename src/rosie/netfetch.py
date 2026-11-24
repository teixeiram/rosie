"""Fetch a public web page for recipe import, safely.

The backend runs inside the home network, so a pasted URL must never reach
the Pi, the router, the modem or anything else on the LAN/tailnet (SSRF).
Each hop (including redirects) must be http(s) on port 80/443 and resolve
only to public addresses; after connecting, the peer address is checked again
(protects against DNS answers that change between the check and the connect).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
# An honest user agent. Pretending to be Chrome got HTTP 403 from bot filters
# (budgetbytes.com, 2026-11-19): the UA said Chrome but the TLS handshake said
# Python, which looks like a scraper. Plain clients are let through.
UA = "RosieRecipeImport/1 (household recipe importer)"


class FetchError(Exception):
    """A user-facing reason the page couldn't be fetched."""


@dataclass
class Page:
    url: str
    content_type: str
    text: str


def _private_domains() -> list[str]:
    """Your own domains (ROSIE_PRIVATE_DOMAINS, comma-separated), e.g. one whose
    public DNS points at home-network addresses. Imports from them are refused."""
    raw = os.environ.get("ROSIE_PRIVATE_DOMAINS", "")
    return [d.strip().lower().strip(".") for d in raw.split(",") if d.strip()]


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"Couldn't find the site {host!r}.") from e
    return sorted({i[4][0] for i in infos})


async def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http:// and https:// links can be imported.")
    if not parts.hostname:
        raise FetchError("That doesn't look like a web address.")
    if parts.username or parts.password:
        raise FetchError("Links with a username or password can't be imported.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise FetchError("Only links on the normal web ports can be imported.")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        addrs = [host]
    else:
        if host == "localhost" or host.endswith((".local", ".lan", ".internal", ".home.arpa")):
            raise FetchError("Links to home-network devices can't be imported.")
        if any(host == d or host.endswith("." + d) for d in _private_domains()):
            raise FetchError("Links to home-network devices can't be imported.")
        addrs = await _resolve(host, port)
    if not addrs or not all(_is_public(a) for a in addrs):
        raise FetchError("Links to home-network devices can't be imported.")


def _peer_ok(resp: httpx.Response) -> bool:
    stream = resp.extensions.get("network_stream")
    if stream is None:
        return True  # test transports have no socket
    addr = stream.get_extra_info("server_addr")
    return bool(addr) and _is_public(addr[0])


async def fetch_page(url: str, client: httpx.AsyncClient | None = None) -> Page:
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": UA}, trust_env=False
    )
    try:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            try:
                async with client.stream(
                    "GET", url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.5"}
                ) as resp:
                    if not _peer_ok(resp):
                        raise FetchError("Links to home-network devices can't be imported.")
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise FetchError("The site sent a broken redirect.")
                        url = urljoin(url, loc)
                        continue
                    if resp.status_code in (401, 403, 429) or resp.status_code == 503:
                        raise FetchError(
                            f"The site refused the request (HTTP {resp.status_code}). "
                            "Paste the recipe text instead."
                        )
                    if resp.status_code >= 400:
                        raise FetchError(f"The site answered HTTP {resp.status_code}.")
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and not (
                        ctype.startswith("text/") or "html" in ctype or "json" in ctype
                    ):
                        raise FetchError(f"That link is not a web page ({ctype}).")
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf += chunk
                        if len(buf) > MAX_BYTES:
                            raise FetchError("That page is too large to import.")
                    text = buf.decode(resp.encoding or "utf-8", errors="replace")
                    return Page(url=str(resp.url), content_type=ctype, text=text)
            except httpx.TimeoutException as e:
                raise FetchError("The site took too long to answer.") from e
            except httpx.HTTPError as e:
                raise FetchError(f"Couldn't load the page ({type(e).__name__}).") from e
        raise FetchError("The site redirected too many times.")
    finally:
        if own:
            await client.aclose()
