import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from rosie import usage
from rosie.netfetch import Page
from rosie.sections import kitchen

GOOD = {
    "is_recipe": True,
    "problem": "",
    "title": "Toast",
    "serves": "Serves 1",
    "total_time": "5 minutes",
    "ingredients": ["1 slice bread"],
    "steps": ["Toast the bread."],
    "notes": [],
}


class FakeMessages:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.calls = payload, stop, []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            model=kw["model"],
            stop_reason=self.stop,
            _request_id="req_1",
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def use_fake(app, payload=GOOD, stop="end_turn"):
    fake = FakeMessages(payload, stop)
    app.state.anthropic = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return fake


def test_import_from_text(app, member):
    fake = use_fake(app)
    r = member.post("/api/kitchen/import", json={"text": "toast: bread, toast it"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["markdown"].startswith("# Toast\nServes 1 · 5 minutes\n\n## Ingredients")
    call = fake.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    assert "<page>" in call["messages"][0]["content"]
    assert usage.spent_this_month(app.state.db) == pytest.approx(
        usage.cost_usd(call["model"], 1000, 200)
    )


def test_import_from_url(app, member, monkeypatch):
    use_fake(app)

    async def fake_fetch(url):
        html = '<script type="application/ld+json">{"@type":"Recipe","name":"Toast"}</script>'
        return Page(url=url, content_type="text/html", text=html)

    monkeypatch.setattr(kitchen, "fetch_page", fake_fetch)
    r = member.post("/api/kitchen/import", json={"url": "https://food.example/toast"})
    assert r.status_code == 200, r.text
    assert r.json()["source_url"] == "https://food.example/toast"


def test_import_not_a_recipe_still_records_usage(app, member):
    use_fake(app, {**GOOD, "is_recipe": False, "problem": "This is a news article."})
    r = member.post("/api/kitchen/import", json={"text": "news"})
    assert r.status_code == 422 and "news article" in r.json()["detail"]
    assert usage.spent_this_month(app.state.db) > 0


def test_import_refusal(app, member):
    use_fake(app, GOOD, stop="refusal")
    r = member.post("/api/kitchen/import", json={"text": "x"})
    assert r.status_code == 422


def test_monthly_cap(app, member):
    fake = use_fake(app)
    usage.record(
        app.state.db,
        user_id=None,
        feature="t",
        model="claude-opus-5-5",
        input_tokens=1_000_000,
        output_tokens=0,
    )  # $4 > $1 cap
    r = member.post("/api/kitchen/import", json={"text": "x"})
    assert r.status_code == 429 and not fake.calls


def test_import_needs_key(app, member):
    app.state.settings = app.state.settings.__class__(
        **{**app.state.settings.__dict__, "anthropic_api_key": ""}
    )
    assert member.post("/api/kitchen/import", json={"text": "x"}).status_code == 503


def test_send_and_save_and_list(app, member):
    sent = []
    app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200))
    )
    md = "# Toast\n\n## Steps\n1. Toast."
    r = member.post("/api/kitchen/send", json={"markdown": md})
    assert r.status_code == 200 and len(sent) == 1
    r = member.post("/api/kitchen/recipes", json={"markdown": md, "source_url": "https://x"})
    rid = r.json()["id"]
    lst = member.get("/api/kitchen/recipes").json()["recipes"]
    assert lst[0]["title"] == "Toast" and lst[0]["by"] == "Sam"
    assert member.get(f"/api/kitchen/recipes/{rid}").json()["markdown"] == md
    assert member.get("/api/kitchen/recipes/999").status_code == 404


def test_kitchen_requires_login(anon):
    assert anon.get("/api/kitchen/status").status_code == 401
    assert anon.post("/api/kitchen/import", json={"text": "x"}).status_code == 401


def test_saved_search_and_meta(member):
    for md in (
        "# Beef Stew\nServes 6 · 2 hours\n\n## Ingredients\n- beef\n- carrots",
        "# Carrot Cake\nServes 12 · 1 hour\n\n## Ingredients\n- carrots\n- flour",
        "# Plain Toast\n\n## Ingredients\n- bread",
    ):
        assert member.post("/api/kitchen/recipes", json={"markdown": md}).status_code == 200
    allr = member.get("/api/kitchen/recipes").json()["recipes"]
    metas = {r["title"]: r["meta"] for r in allr}
    assert metas["Beef Stew"] == "Serves 6 · 2 hours" and metas["Plain Toast"] == ""
    hits = [r["title"] for r in member.get("/api/kitchen/recipes?q=carrot").json()["recipes"]]
    assert hits == ["Carrot Cake", "Beef Stew"]  # title match first
    both = member.get("/api/kitchen/recipes?q=BEEF carrots").json()["recipes"]
    assert [r["title"] for r in both] == ["Beef Stew"]
    assert member.get("/api/kitchen/recipes?q=zzz").json()["recipes"] == []


def test_metrics_report_spend_without_login(member):
    from rosie import usage

    app = member.app
    usage.record(
        app.state.db,
        user_id=None,
        feature="chat",
        model="claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    member.cookies.clear()
    r = member.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "rosie_ai_spend_month_usd 2.000000" in body
    assert "rosie_ai_spend_usd_total 2.000000" in body
    assert "rosie_ai_requests_total 1" in body
    assert "rosie_ai_cap_usd" in body


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()
JPG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\0" * 64).decode()
PDF = base64.b64encode(b"%PDF-1.7\n" + b"\0" * 64).decode()


def test_import_from_screenshots(app, member):
    fake = use_fake(app)
    files = [{"media_type": "image/png", "data": PNG}, {"media_type": "image/jpeg", "data": JPG}]
    r = member.post("/api/kitchen/import-files", json={"files": files, "note": "from Instagram"})
    assert r.status_code == 200, r.text
    assert r.json()["markdown"].startswith("# ")
    content = fake.calls[0]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "image", "text"]
    assert content[0]["text"].startswith("<page>") and "2 images" in content[0]["text"]
    assert "from Instagram" in content[0]["text"]
    assert content[1]["source"] == {"type": "base64", "media_type": "image/png", "data": PNG}
    feat = app.state.db.execute("SELECT feature FROM api_usage").fetchone()[0]
    assert feat == "kitchen.import_files"


def test_import_from_pdf(app, member):
    fake = use_fake(app)
    r = member.post(
        "/api/kitchen/import-files",
        json={"files": [{"media_type": "application/pdf", "data": PDF}]},
    )
    assert r.status_code == 200
    block = fake.calls[0]["messages"][0]["content"][1]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"


@pytest.mark.parametrize(
    "files, code",
    [
        ([{"media_type": "image/svg+xml", "data": PNG}], 400),  # type not allowed
        ([{"media_type": "image/png", "data": JPG}], 400),  # says PNG, is JPEG
        ([{"media_type": "image/png", "data": "not base64!!"}], 400),
        ([{"media_type": "application/pdf", "data": PDF}] * 2, 400),  # one PDF at a time
        ([{"media_type": "image/png", "data": PNG}] * 11, 422),  # over 10 files
        ([], 422),
    ],
)
def test_import_files_rejects(app, member, files, code):
    fake = use_fake(app)
    r = member.post("/api/kitchen/import-files", json={"files": files})
    assert r.status_code == code and not fake.calls


def test_import_files_size_limit(app, member):
    fake = use_fake(app)
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * (5 * 1024 * 1024)).decode()
    r = member.post(
        "/api/kitchen/import-files", json={"files": [{"media_type": "image/png", "data": big}]}
    )
    assert r.status_code == 413 and not fake.calls
