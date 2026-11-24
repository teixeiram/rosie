"""Turn a recipe web page into compact input for the model.

Most recipe sites embed schema.org Recipe data as JSON-LD; when present it is
the cleanest source. The visible text (scripts, styles, nav and footers
removed) goes along as a fallback and for notes the JSON-LD lacks.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

_SKIP = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "nav",
    "footer",
    "header",
    "form",
    "iframe",
    "button",
    "select",
    "aside",
}
_BLOCK = {
    "p",
    "div",
    "li",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "section",
    "article",
    "ul",
    "ol",
    "table",
    "blockquote",
    "pre",
    "dd",
    "dt",
}
_VOID = {
    "br",
    "img",
    "hr",
    "meta",
    "link",
    "input",
    "source",
    "wbr",
    "area",
    "base",
    "col",
    "embed",
    "param",
    "track",
}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # Skipping a subtree: the tag that started it and its nesting count
        # (only that tag is counted, so unclosed <li>/<p> inside can't stall it).
        self.skip_tag = ""
        self.skip_depth = 0
        self.in_jsonld = False
        self.jsonld: list[str] = []
        self.parts: list[str] = []
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld.append("")
        if tag == "title":
            self.in_title = True
        if tag in _VOID:
            if tag == "br":
                self.parts.append("\n")
            return
        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag in _SKIP:
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self.in_jsonld = False
        if tag == "title":
            self.in_title = False
        if tag in _VOID:
            return
        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth -= 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld[-1] += data
        elif self.in_title:
            self.title += data
        elif not self.skip_depth:
            self.parts.append(data)


def _find_recipes(node) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, list):
        for n in node:
            found += _find_recipes(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Recipe" in types:
            found.append(node)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found += _find_recipes(node[key])
    return found


_KEEP = (
    "name",
    "description",
    "recipeYield",
    "prepTime",
    "cookTime",
    "totalTime",
    "recipeIngredient",
    "recipeInstructions",
    "recipeCategory",
    "recipeCuisine",
    "recipeNotes",
    "suitableForDiet",
)


def _slim(recipe: dict) -> dict:
    return {k: recipe[k] for k in _KEEP if k in recipe}


def extract(html: str) -> tuple[list[dict], str, str]:
    """Return (schema.org recipes, page title, visible text)."""
    p = _Parser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed markup: use what we got
        pass
    recipes: list[dict] = []
    for raw in p.jsonld:
        try:
            recipes += _find_recipes(json.loads(raw.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return [_slim(r) for r in recipes], " ".join(p.title.split()), text
