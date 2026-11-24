import importlib.util
import pathlib
import time

import httpx
import pytest

from rosie.hubformat import Recipe, SendError, render, send_to_kitchen, sign, title_of

RELAY = pathlib.Path.home() / "docker/homeassistant/recipe-relay/recipe.py"


def test_render_hub_format():
    md = render(
        Recipe(
            title="Pancakes",
            serves="Serves 4",
            total_time="20 minutes",
            ingredients=["- 2 cups flour", "1 egg"],
            steps=["Mix.", " Cook  on a hot pan. "],
            notes=[],
        )
    )
    assert md == (
        "# Pancakes\nServes 4 · 20 minutes\n\n## Ingredients\n- 2 cups flour\n- 1 egg\n\n"
        "## Steps\n1. Mix.\n2. Cook on a hot pan."
    )
    assert title_of(md) == "Pancakes"


def test_render_notes_and_no_meta():
    md = render(Recipe(title="X", ingredients=["a"], steps=["b"], notes=["keep cold"]))
    assert md.splitlines()[1] == ""
    assert md.endswith("## Notes\n- keep cold")


def test_signature_known_vector():
    # sha256("s\n1700000000\nbody\ns")
    out = sign("s", "body", ts=1700000000)
    assert out.startswith("body\nsig:1700000000:")
    assert len(out.rsplit(":", 1)[1]) == 64


@pytest.mark.skipif(not RELAY.exists(), reason="recipe-relay not on this machine")
def test_signature_accepted_by_recipe_relay():
    spec = importlib.util.spec_from_file_location("relay_recipe", RELAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    body = render(Recipe(title="Soup", ingredients=["1 l stock"], steps=["Heat it."]))
    got_body, _ = mod.verify("k" * 64, sign("k" * 64, body), now=time.time())
    assert got_body == body
    parsed = mod.parse(got_body)
    assert parsed["title"] == "Soup" and parsed["step_list"] == ["Heat it."]


async def test_send_posts_signed_body():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await send_to_kitchen(
            c, "https://ntfy.test", "recipes-x", "sec", "# T\r\n\n## Steps\n1. a\n"
        )
    assert seen["url"] == "https://ntfy.test/recipes-x"
    assert seen["body"].startswith("# T\n\n## Steps\n1. a\nsig:")


async def test_send_errors():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
        with pytest.raises(SendError, match="HTTP 500"):
            await send_to_kitchen(c, "https://n", "t", "s", "# x")
        with pytest.raises(SendError, match="not configured"):
            await send_to_kitchen(c, "https://n", "", "", "# x")
        with pytest.raises(SendError, match="too long"):
            await send_to_kitchen(c, "https://n", "t", "s", "x" * 70_000)
