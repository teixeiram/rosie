"""One-time bulk import of recipe PDFs (e.g. the Claude "Recipes" project export).

    docker compose exec app python -m rosie.import_pdfs /data/import --as alex [--dry-run]

Each PDF's text goes through the same Claude extraction as website import
(recipe_ai), so two-column layouts that scramble the text get put back
together, and is stored in the shared collection in Hub format. Recipes whose
title is already in the collection are skipped, so re-running is safe. Stops
before the monthly AI cap is reached.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import anthropic
import pypdf

from . import db, usage
from .config import Settings
from .hubformat import render, title_of
from .recipe_ai import ExtractError, build_input, extract_recipe

CONCURRENCY = 4


def pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _guess_title(path: Path) -> str:
    name = path.stem
    return name[: -len(" Recipe")] if name.endswith(" Recipe") else name


async def run(folder: Path, username: str, dry_run: bool) -> int:
    s = Settings.from_env()
    conn = db.connect(s.db_path)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        print(f"No user {username!r}.", file=sys.stderr)
        return 2
    user_id = row["id"]
    if not s.anthropic_api_key and not dry_run:
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    have = {r[0].casefold() for r in conn.execute("SELECT title FROM recipes")}
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")
    todo = [p for p in files if _guess_title(p).casefold() not in have]
    print(f"{len(files)} PDFs, {len(files) - len(todo)} already in the collection by file name.")
    if dry_run:
        for p in todo:
            print(f"  would import: {p.name} ({len(pdf_text(p))} chars)")
        return 0

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, timeout=180, max_retries=3)
    sem = asyncio.Semaphore(CONCURRENCY)
    counts = {"added": 0, "skipped": 0, "failed": 0}
    stop = False

    async def one(path: Path) -> None:
        nonlocal stop
        async with sem:
            if stop:
                return
            if usage.spent_this_month(conn) >= s.monthly_cap_usd:
                stop = True
                print("Monthly AI cap reached; stopping. Raise ROSIE_MONTHLY_CAP_USD and re-run.")
                return
            try:
                text = pdf_text(path)
            except Exception as e:  # unreadable PDF
                counts["failed"] += 1
                print(f"  FAILED {path.name}: can't read PDF ({type(e).__name__})")
                return
            content, truncated = build_input(page_title=_guess_title(path), text=text)

            def on_usage(model, tin, tout, rid):
                usage.record(
                    conn,
                    user_id=user_id,
                    feature="kitchen.bulk_import",
                    model=model,
                    input_tokens=tin,
                    output_tokens=tout,
                    request_id=rid,
                )

            try:
                result = await extract_recipe(client, s.model, content, truncated, on_usage)
            except ExtractError as e:
                counts["failed"] += 1
                print(f"  FAILED {path.name}: {e}")
                return
            md = render(result.recipe)
            title = title_of(md)[:200] or _guess_title(path)
            if title.casefold() in have:
                counts["skipped"] += 1
                print(f"  skipped {path.name}: '{title}' is already in the collection")
                return
            have.add(title.casefold())
            conn.execute(
                "INSERT INTO recipes (title, markdown, source_url, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (title, md, f"file:{path.name}", user_id, int(time.time())),
            )
            counts["added"] += 1
            print(f"  added {title}")

    start = usage.spent_this_month(conn)
    await asyncio.gather(*(one(p) for p in todo))
    await client.close()
    spent = usage.spent_this_month(conn) - start
    print(
        f"Done: {counts['added']} added, {counts['skipped']} skipped, "
        f"{counts['failed']} failed. AI cost ${spent:.2f}."
    )
    return 0 if not counts["failed"] else 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rosie.import_pdfs")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--as", dest="username", required=True, help="account the recipes belong to")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    sys.exit(asyncio.run(run(a.folder, a.username, a.dry_run)))


if __name__ == "__main__":
    main()
