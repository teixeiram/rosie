import json
from types import SimpleNamespace

import pytest

from rosie import usage
from rosie.recipe_chat import ChatError, validate_messages

STEW = "# Beef Stew\nServes 4\n\n## Ingredients\n- 1 lb beef\n\n## Steps\n1. Brown the beef."


class FakeMessages:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            model=kw["model"],
            stop_reason="end_turn",
            _request_id="req_2",
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=300,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=60_000,
            ),
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def setup(app, payload):
    fake = FakeMessages(payload)
    app.state.anthropic = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    rid = app.state.db.execute(
        "INSERT INTO recipes (title, markdown, created_at) VALUES ('Beef Stew', ?, 0)", (STEW,)
    ).lastrowid
    return fake, rid


def test_validate_messages():
    ok = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "stew?"},
    ]
    assert len(validate_messages(ok)) == 3
    for bad in (
        [],
        [{"role": "assistant", "content": "x"}],
        [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
        ok[:2],
        [{"role": "user", "content": "x" * 9000}],
    ):
        with pytest.raises(ChatError):
            validate_messages(bad)


def test_chat_shows_saved_and_adapted(app, member):
    payload = {
        "reply": "Your beef stew works. Here it is doubled.",
        "saved_recipe_titles": ["Beef Stew", "# beef stew", "Mulligatawny"],
        "recipes": [
            {
                "title": "Beef Stew for 8",
                "serves": "Serves 8",
                "total_time": "",
                "ingredients": ["2 lb beef"],
                "steps": ["Brown the beef."],
                "notes": [],
            }
        ],
    }
    fake, rid = setup(app, payload)
    r = member.post(
        "/api/kitchen/chat", json={"messages": [{"role": "user", "content": "stew for 8?"}]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["id"] for s in body["saved"]] == [rid]  # deduped, unknown title dropped
    assert body["recipes"][0]["markdown"].startswith(
        "# Beef Stew for 8\nServes 8\n\n## Ingredients"
    )
    assert "[Showed saved recipe" in body["history_text"]
    call = fake.calls[0]
    assert call["model"] == app.state.settings.chat_model == "claude-sonnet-5-5"
    collection = call["system"][1]
    assert collection["cache_control"] == {"type": "ephemeral"}
    assert "<recipe>\n# Beef Stew" in collection["text"] and "Brown the beef" in collection["text"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    # cache reads are billed at a fraction of the input price
    spent = usage.spent_this_month(app.state.db)
    assert spent == pytest.approx(usage.cost_usd(call["model"], 50, 300, 0, 60_000))
    assert spent < usage.cost_usd(call["model"], 60_050, 300)


def test_chat_rejects_bad_history(app, member):
    setup(app, {"reply": "", "saved_recipe_titles": [], "recipes": []})
    r = member.post(
        "/api/kitchen/chat", json={"messages": [{"role": "assistant", "content": "hi"}]}
    )
    assert r.status_code == 422


def test_chat_respects_cap(app, member):
    fake, _ = setup(app, {"reply": "", "saved_recipe_titles": [], "recipes": []})
    usage.record(
        app.state.db,
        user_id=None,
        feature="t",
        model="claude-opus-5-5",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    r = member.post("/api/kitchen/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 429 and not fake.calls


def test_chats_are_saved_continued_and_private(app, admin, member):
    payload = {"reply": "Try the stew.", "saved_recipe_titles": ["Beef Stew"], "recipes": []}
    fake, _ = setup(app, payload)
    q1 = [{"role": "user", "content": "Something warm for a cold night please"}]
    r1 = member.post("/api/kitchen/chat", json={"messages": q1}).json()
    cid = r1["chat_id"]
    lst = member.get("/api/kitchen/chats").json()["chats"]
    assert lst[0]["id"] == cid and lst[0]["title"] == "Something warm for a cold night please"
    assert lst[0]["questions"] == 1

    q2 = q1 + [
        {"role": "assistant", "content": r1["history_text"]},
        {"role": "user", "content": "For 8"},
    ]
    r2 = member.post("/api/kitchen/chat", json={"messages": q2, "chat_id": cid}).json()
    assert r2["chat_id"] == cid
    chat = member.get(f"/api/kitchen/chats/{cid}").json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "user", "assistant"]
    d = chat["display"]
    assert [x.get("role") or x["card"] for x in d] == ["user", "assistant", "saved"] * 2
    assert d[2]["title"] == "Beef Stew" and d[2]["markdown"].startswith("# Beef Stew")
    assert member.get("/api/kitchen/chats").json()["chats"][0]["questions"] == 2

    # Private: Mike can't see, continue or delete Sam's chat.
    assert admin.get("/api/kitchen/chats").json()["chats"] == []
    assert admin.get(f"/api/kitchen/chats/{cid}").status_code == 404
    calls = len(fake.calls)
    r = admin.post("/api/kitchen/chat", json={"messages": q1, "chat_id": cid})
    assert r.status_code == 404 and len(fake.calls) == calls  # refused before paying
    assert admin.delete(f"/api/kitchen/chats/{cid}").status_code == 404

    assert member.delete(f"/api/kitchen/chats/{cid}").json() == {"ok": True}
    assert member.get("/api/kitchen/chats").json()["chats"] == []


def test_long_first_question_makes_short_title(app, member):
    setup(app, {"reply": "ok", "saved_recipe_titles": [], "recipes": []})
    q = [{"role": "user", "content": "word " * 40}]
    cid = member.post("/api/kitchen/chat", json={"messages": q}).json()["chat_id"]
    title = member.get(f"/api/kitchen/chats/{cid}").json()["title"]
    assert len(title) <= 60 and title.endswith("…")
