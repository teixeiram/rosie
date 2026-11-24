"""API usage log and the monthly spending cap for paid model calls."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime

# USD per million tokens (input, output), Anthropic first-party list prices.
# Unknown models are costed at the highest listed rate so the cap stays safe.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
}
_FALLBACK_PRICE = (10.0, 50.0)


# Prompt caching: writes cost 1.25x the input price (5-minute TTL), reads 0.1x
# (Opus 5.5 lists cache reads at $0.20/MTok, 0.05x; 0.1x keeps the estimate safe).
CACHE_WRITE = 1.25
CACHE_READ = 0.1


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    pin, pout = PRICES.get(model, _FALLBACK_PRICE)
    effective_in = input_tokens + cache_write_tokens * CACHE_WRITE + cache_read_tokens * CACHE_READ
    return (effective_in * pin + output_tokens * pout) / 1_000_000


def month_start(now: float | None = None) -> int:
    d = datetime.fromtimestamp(now or time.time(), UTC)
    return int(datetime(d.year, d.month, 1, tzinfo=UTC).timestamp())


def spent_this_month(conn: sqlite3.Connection, now: float | None = None) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage WHERE at >= ?", (month_start(now),)
    ).fetchone()
    return float(row[0])


def record(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    request_id: str | None = None,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    c = cost_usd(model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens)
    # input_tokens stored = everything read, so usage reports show the true volume.
    input_tokens = input_tokens + cache_write_tokens + cache_read_tokens
    conn.execute(
        "INSERT INTO api_usage (at, user_id, feature, model, input_tokens, output_tokens,"
        " cost_usd, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(time.time()), user_id, feature, model, input_tokens, output_tokens, c, request_id),
    )
    return c


def summary(conn: sqlite3.Connection, cap: float) -> dict:
    rows = conn.execute(
        "SELECT feature, COUNT(*) AS calls, SUM(cost_usd) AS cost FROM api_usage"
        " WHERE at >= ? GROUP BY feature ORDER BY cost DESC",
        (month_start(),),
    ).fetchall()
    return {
        "month_spent_usd": round(spent_this_month(conn), 4),
        "monthly_cap_usd": cap,
        "by_feature": [
            {"feature": r["feature"], "calls": r["calls"], "cost_usd": round(r["cost"], 4)}
            for r in rows
        ],
    }
