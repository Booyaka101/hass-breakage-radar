"""Publishes the announced removals as an RSS feed.

Asked for on the announcement thread: "Can I have an RSS feed? I don't want to
crawl." The index is a snapshot, so following it means diffing it yourself.
The feed answers the other question, which is what is *new*, and it costs
nothing to host because the crawl already publishes static files.

One item per rule, dated the first time the crawl published it. That date is
kept in ``state/feed.json``, because a rule carries the release it breaks in,
not the day it was announced.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from tools.rules_engine import parse_version

BOARD = "https://booyaka101.github.io/hass-breakage-radar/"
FEED_URL = f"{BOARD}feed.xml"

#: Enough for a reader that has been away for months, without shipping the lot.
MAX_ITEMS = 60


def _rfc822(value: str) -> str:
    """ISO 8601 to the date format RSS readers expect."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        moment = datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.strftime("%a, %d %b %Y %H:%M:%S +0000")


def update_first_seen(
    rules: list[dict[str, Any]], seen: dict[str, str], *, now: str
) -> dict[str, str]:
    """Record the first time each rule was published. Never rewrites a date."""
    for rule in rules:
        seen.setdefault(rule["id"], now)
    return seen


def describe(rule: dict[str, Any], repos_hit: int) -> str:
    parts = [rule.get("message", "").strip()]
    if repos_hit:
        parts.append(
            f"{repos_hit} integration(s) in the HACS catalogue currently use it."
        )
    if rule.get("replacement"):
        parts.append(f"Replacement: {rule['replacement']}.")
    parts.append(f"Confidence: {rule.get('confidence', 'medium')}.")
    return " ".join(p for p in parts if p)


def build(payload: dict[str, Any], seen: dict[str, str]) -> str:
    """Render the feed for a built index payload."""
    generated = payload.get("generated_utc", "")
    items = sorted(
        payload.get("rules", []),
        key=lambda r: (
            seen.get(r["id"], generated),
            # Newest first, and on the first run every rule shares a date, so
            # fall back to the soonest deadline rather than an arbitrary id.
            [-part for part in parse_version(r["breaks_in"])],
        ),
        reverse=True,
    )[:MAX_ITEMS]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        "    <title>Breakage Radar: Home Assistant API removals</title>",
        f"    <link>{BOARD}</link>",
        "    <description>Home Assistant APIs scheduled for removal, and how "
        "many HACS custom integrations still use each one.</description>",
        "    <language>en</language>",
        f"    <lastBuildDate>{_rfc822(generated)}</lastBuildDate>",
        f'    <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml"/>',
    ]
    for rule in items:
        published = seen.get(rule["id"], generated)
        title = f"{rule['breaks_in']}: {rule.get('symbol') or rule['id']}"
        link = rule.get("source_url") or rule.get("source") or BOARD
        if not str(link).startswith("http"):
            link = BOARD
        lines += [
            "    <item>",
            f"      <title>{html.escape(title)}</title>",
            f"      <link>{html.escape(link)}</link>",
            f'      <guid isPermaLink="false">breakage-radar:{html.escape(rule["id"])}</guid>',
            f"      <pubDate>{_rfc822(published)}</pubDate>",
            f"      <category>{html.escape(rule['breaks_in'])}</category>",
            f"      <description>{html.escape(describe(rule, rule.get('repos_hit', 0)))}</description>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)
