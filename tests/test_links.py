import re

import pytest

from rosie import links
from rosie.app import create_app
from tests.conftest import make_settings

LINKS = """
[[links]]
label = "Home Assistant"
url = "https://ha.example/"
[[links]]
label = "Grafana"
url = "http://grafana.example:3001/"
admin = true
"""


@pytest.fixture
def settings(tmp_path):
    p = tmp_path / "links.toml"
    p.write_text(LINKS)
    return make_settings(tmp_path, links_config=p)


def test_me_lists_links_by_role(admin, member):
    assert member.get("/api/me").json()["links"] == [
        {"label": "Home Assistant", "url": "https://ha.example/"}
    ]
    assert [x["label"] for x in admin.get("/api/me").json()["links"]] == [
        "Home Assistant",
        "Grafana",
    ]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "ftp://x/", "https://"])
def test_only_http_links(tmp_path, url):
    p = tmp_path / "l.toml"
    p.write_text(f'[[links]]\nlabel = "x"\nurl = "{url}"\n')
    with pytest.raises(ValueError):
        links.load(p)


def test_bad_file_does_not_break_the_app(tmp_path):
    p = tmp_path / "l.toml"
    p.write_text('[[links]]\nlabel = "x"\nurl = "javascript:1"\n')
    app = create_app(make_settings(tmp_path, links_config=p))
    assert app.state.links == []


def test_missing_file_means_no_links(tmp_path):
    assert links.load(tmp_path / "nope.toml") == []


def test_repo_links_file_is_valid():
    from rosie.config import _DEFAULT_LINKS

    assert len(links.load(_DEFAULT_LINKS)) >= 2


def test_static_files_revalidate_and_are_versioned(anon):
    client = anon
    # phones kept running an old app.js after a deploy.
    html = client.get("/").text

    js = re.search(r'src="/static/app\.js\?v=([0-9a-f]{12})"', html)
    css = re.search(r'href="/static/style\.css\?v=([0-9a-f]{12})"', html)
    assert js and css
    r = client.get(f"/static/app.js?v={js.group(1)}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"
