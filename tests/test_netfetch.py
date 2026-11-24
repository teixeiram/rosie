import httpx
import pytest

from rosie import netfetch
from rosie.netfetch import FetchError, check_url, fetch_page


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "http://127.0.0.1/",
        "http://192.168.1.10:8123/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://100.64.0.7/",  # tailscale CGNAT
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:192.168.1.10]/",
        "http://localhost/",
        "http://printer.local/",
        "https://example.com:8443/",
        "https://user:pw@example.com/",
    ],
)
async def test_blocked(url):
    with pytest.raises(FetchError):
        await check_url(url)


@pytest.mark.parametrize("url", ["https://ha.example.net/", "https://example.net/"])
async def test_private_domains_are_blocked(monkeypatch, url):
    monkeypatch.setenv("ROSIE_PRIVATE_DOMAINS", "example.net, other.test")
    with pytest.raises(FetchError):
        await check_url(url)


async def test_hostname_resolving_to_private_is_blocked(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14", "192.168.1.5"]

    monkeypatch.setattr(netfetch, "_resolve", fake_resolve)
    with pytest.raises(FetchError):
        await check_url("https://sneaky.example/")


async def test_public_ok(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(netfetch, "_resolve", fake_resolve)
    await check_url("https://www.example.com/recipe")


async def test_redirect_to_private_is_blocked(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(netfetch, "_resolve", fake_resolve)

    def handler(req):
        return httpx.Response(302, headers={"location": "http://192.168.1.10/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError, match="home-network"):
            await fetch_page("https://site.example/r", client=c)


async def test_fetch_follows_redirect_and_reads(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(netfetch, "_resolve", fake_resolve)

    def handler(req):
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, html="<p>hello</p>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        page = await fetch_page("https://site.example/a", client=c)
    assert page.url.endswith("/b") and "hello" in page.text


async def test_fetch_refused_and_binary(monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.215.14"]

    monkeypatch.setattr(netfetch, "_resolve", fake_resolve)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as c:
        with pytest.raises(FetchError, match="Paste the recipe text"):
            await fetch_page("https://site.example/", client=c)
    pdf = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pdf)) as c:
        with pytest.raises(FetchError, match="not a web page"):
            await fetch_page("https://site.example/", client=c)
