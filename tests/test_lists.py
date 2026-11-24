import asyncio
import json
from types import SimpleNamespace

from rosie.grocery import SECTIONS, is_staple, item_name, keyword_section
from rosie.sections.lists import Changes, categorize_pending


def test_item_name_and_keywords():
    assert item_name("2 lb boneless chicken thighs, trimmed") == "boneless chicken thighs"
    assert item_name("1 (14-ounce) can coconut milk") == "coconut milk"
    assert keyword_section("1 cup chicken broth") == "Canned & Dry Goods"  # phrase beats "chicken"
    assert keyword_section("2 lb chicken thighs") == "Meat & Seafood"
    assert keyword_section("dragonfruit") is None
    assert is_staple("Kosher salt") and is_staple("¼ cup all-purpose flour")
    assert not is_staple("2 lb beef")


def get(client, list_id):
    return client.get(f"/api/lists/{list_id}").json()


def test_lists_seeded_and_members_can_use(member):
    names = [x["name"] for x in member.get("/api/lists").json()["lists"]]
    assert names == ["Costco", "Meijer", "To-do"]
    me = member.get("/api/me").json()
    assert "lists" not in [s["id"] for s in me["sections"]]  # not a bottom tab
    assert me["parts"]["lists"] is True


def test_add_sections_duplicates_and_check(member):
    r = member.post("/api/lists/2/items", json={"texts": ["milk; 2 lb chicken thighs\nbananas"]})
    assert r.status_code == 200 and len(r.json()["added"]) == 3
    r = member.post("/api/lists/2/items", json={"texts": ["Milk"]})
    assert r.json()["skipped_duplicates"] == ["Milk"]
    data = get(member, 2)
    secs = {i["text"]: i["section"] for i in data["items"]}
    assert secs == {
        "milk": "Dairy & Eggs",
        "2 lb chicken thighs": "Meat & Seafood",
        "bananas": "Produce",
    }
    milk = next(i for i in data["items"] if i["text"] == "milk")
    assert member.patch(f"/api/lists/items/{milk['id']}", json={"checked": True}).status_code == 200
    data = get(member, 2)
    assert [i["checked"] for i in data["items"]][-1] is True  # checked sort last
    # once checked, the same item can be added again
    assert member.post("/api/lists/2/items", json={"texts": ["milk"]}).json()["added"] == ["milk"]
    assert member.post("/api/lists/2/clear-checked").json()["removed"] == 1


def test_todo_list_has_no_sections(member):
    member.post("/api/lists/3/items", json={"texts": ["call plumber"]})
    item = get(member, 3)["items"][0]
    assert item["section"] is None
    assert member.put("/api/lists/3/sections", json={"sections": SECTIONS}).status_code == 400


def test_user_choices_are_remembered(member):
    member.post("/api/lists/2/items", json={"texts": ["sparkling water"]})
    item = get(member, 2)["items"][0]
    member.patch(f"/api/lists/items/{item['id']}", json={"section": "Snacks", "aisle": "14"})
    member.delete(f"/api/lists/items/{item['id']}")
    member.post("/api/lists/2/items", json={"texts": ["Sparkling water"]})
    again = get(member, 2)["items"][0]
    assert again["section"] == "Snacks" and again["aisle"] == "14"
    # section is shared across stores, aisle is per store
    member.post("/api/lists/1/items", json={"texts": ["sparkling water"]})
    costco = get(member, 1)["items"][0]
    assert costco["section"] == "Snacks" and costco["aisle"] == ""
    assert (
        member.patch(f"/api/lists/items/{again['id']}", json={"section": "Nope"}).status_code == 400
    )


def test_reorder_sections(member):
    new = list(reversed(SECTIONS))
    assert member.put("/api/lists/1/sections", json={"sections": new}).status_code == 200
    assert get(member, 1)["sections"] == new
    assert member.put("/api/lists/1/sections", json={"sections": new[:-1]}).status_code == 400


def test_parse_ingredients(member):
    md = (
        "# Stew\nServes 4\n\n## Ingredients\n- 1 lb beef\n- Kosher salt\n- 2 carrots\n\n"
        "## Steps\n1. Cook.\n\n## Notes\n- not an ingredient"
    )
    items = member.post("/api/lists/parse-ingredients", json={"markdown": md}).json()["items"]
    assert [(i["text"], i["staple"]) for i in items] == [
        ("1 lb beef", False),
        ("Kosher salt", True),
        ("2 carrots", False),
    ]


def test_lists_require_login(anon):
    assert anon.get("/api/lists").status_code == 401
    assert anon.post("/api/lists/1/items", json={"texts": ["x"]}).status_code == 401


async def test_changes_long_poll():
    c = Changes()
    rev = c.rev
    assert await c.wait(rev - 5, 1) == rev  # stale revision returns at once
    waiter = asyncio.create_task(c.wait(rev, 5))
    await asyncio.sleep(0.05)
    c.bump()
    assert await asyncio.wait_for(waiter, 1) == rev + 1
    assert await c.wait(rev + 1, 0.05) == rev + 1  # timeout returns current


class FakeMessages:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            model=kw["model"],
            stop_reason="end_turn",
            _request_id="r",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


async def test_ai_categorizes_unknown_items(app):
    db = app.state.db
    db.execute(
        "INSERT INTO list_items (list_id, text, item_key, added_at)"
        " VALUES (2, '3 dragonfruit', 'dragonfruit', 0), (2, 'zzz', 'zzz', 0)"
    )
    fake = FakeMessages({"items": [{"name": "dragonfruit", "section": "Produce"}]})
    app.state.anthropic = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    await categorize_pending(app, None)
    rows = dict(db.execute("SELECT item_key, section FROM list_items").fetchall())
    assert rows == {"dragonfruit": "Produce", "zzz": "Other"}
    assert (
        db.execute("SELECT source FROM item_sections WHERE item_key='dragonfruit'").fetchone()[0]
        == "ai"
    )
    assert "<items>" in fake.calls[0]["messages"][0]["content"]
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"


async def test_categorize_falls_back_without_key(app, settings):
    app.state.settings = settings.__class__(**{**settings.__dict__, "anthropic_api_key": ""})
    app.state.db.execute(
        "INSERT INTO list_items (list_id, text, item_key, added_at) VALUES (1, 'gizmo', 'gizmo', 0)"
    )
    await categorize_pending(app, None)
    assert app.state.db.execute("SELECT section FROM list_items").fetchone()[0] == "Other"
