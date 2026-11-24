def test_today_summary(app, member, admin):
    db = app.state.db
    todo = db.execute("SELECT id FROM lists WHERE kind = 'todo'").fetchone()[0]
    costco = db.execute("SELECT id FROM lists WHERE name = 'Costco'").fetchone()[0]
    member.post(f"/api/lists/{todo}/items", json={"texts": ["call plumber", "renew passport"]})
    member.post(f"/api/lists/{costco}/items", json={"texts": ["eggs; milk; coffee"]})
    r = admin.get("/api/today").json()
    assert r["name"] == "Mike"
    assert r["todo"]["list_id"] == todo and r["todo"]["open"] == 2
    assert [i["text"] for i in r["todo"]["items"]] == ["call plumber", "renew passport"]
    assert r["todo"]["items"][0]["by"] == "Sam"
    shop = {s["name"]: s["open"] for s in r["shopping"]}
    assert shop["Costco"] == 3 and shop["Meijer"] == 0 and "To-do" not in shop

    first = r["todo"]["items"][0]["id"]
    member.patch(f"/api/lists/items/{first}", json={"checked": True})
    assert admin.get("/api/today").json()["todo"]["open"] == 1


def test_today_is_first_and_needs_login(member, anon):
    assert member.get("/api/me").json()["sections"][0]["id"] == "today"
    assert anon.get("/api/today").status_code == 401
