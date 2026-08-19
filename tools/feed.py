"""Publishes the announced removals as an RSS feed.

Asked for on the announcement thread: "Can I have an RSS feed? I don't want to
crawl." The index is a snapshot, so following it means diffing it yourself.
The feed answers the other question, which is what is *new*, and it costs
nothing to host because the crawl already publishes static files.

One item per Home Assistant release, carrying that release's own content: what
it removes, and which HACS integrations still use it. A release is the unit
people follow, and it gives a reader something to read in place.

An item is news when a rule joins the release. The integration count moves
every day as the crawl widens, and dating the item on that would re-notify
every subscriber daily, so ``pubDate`` is the newest first-seen date among the
release's rules. Those dates live in ``state/feed.json``, keyed by rule,
because a rule carries the release it breaks in and not the day it was
announced.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

from tools.rules_engine import parse_version

BOARD = "https://booyaka101.github.io/hass-breakage-radar/"
FEED_URL = f"{BOARD}feed.xml"

#: Sits next to feed.xml in docs/. Stays relative: browsers refuse a
#: cross-origin XSL, so an absolute URL would silently stop it rendering.
STYLESHEET = "feed.xsl"

#: Releases kept in the feed. Deadlines are announced about a year ahead, so
#: this is years of history rather than the handful that are live.
MAX_ITEMS = 12

#: Integrations listed inside one item. The whole table for the biggest release
#: is over 370 KB of markup, which is not something to put in a file every
#: subscriber refetches; the rest are one click away on the board.
MAX_TABLE_ROWS = 20

#: Long enough for a symbol, short enough to read in a list. Prose rules carry
#: a sentence where the others carry a name.
MAX_LABEL = 72


def _rfc822(value: str) -> str:
    """ISO 8601 to the date format RSS readers expect."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        moment = datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _cdata(markup: str) -> str:
    """Wrap generated HTML so readers get markup rather than escaped text."""
    return "<![CDATA[" + markup.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``1 integration`` / ``9 integrations``, since a sentence cannot say (s).

    Same helper as the integration's repairs.py. Not imported from there:
    tools/ and custom_components/ stay separable, which is why the engine is
    vendored rather than shared.
    """
    return singular if count == 1 else (plural_form or f"{singular}s")


def anchor_for(release: str) -> str:
    """The board section this release renders into."""
    return f"{BOARD}#release-{release}"


def update_first_seen(
    rules: list[dict[str, Any]], seen: dict[str, str], *, now: str
) -> dict[str, str]:
    """Record the first time each rule was published. Never rewrites a date."""
    for rule in rules:
        seen.setdefault(rule["id"], now)
    return seen


def rule_label(rule: dict[str, Any]) -> str:
    """The symbol a rule is about, or a trimmed sentence for prose rules.

    ``kind: prose`` rules have no symbol -- the symbol is the sentence -- so
    they get the sentence cut on a word boundary rather than a dangling
    ``'{...}'`` placeholder or the rule's slug.
    """
    symbol = " ".join((rule.get("symbol") or "").split())
    name = symbol or rule["id"]
    if len(name) > MAX_LABEL:
        name = name[: MAX_LABEL - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return name


def title_for(release: str) -> str:
    """What the board's own heading says, so the two read as one thing.

    Deliberately carries no count: that changes daily, and a title that moves
    makes an unchanged item look new in some readers.
    """
    return f"Home Assistant {release}"


def guid_for(release: str, rules: list[dict[str, Any]]) -> str:
    """Stable per release, until its rule set changes.

    Folding the rules in is what surfaces a newly announced removal. Readers
    key off the guid, so a fixed one would leave the item sitting unread at its
    original date however much was added to it.
    """
    digest = hashlib.sha256(
        "\n".join(sorted(rule["id"] for rule in rules)).encode("utf-8")
    ).hexdigest()[:8]
    return f"breakage-radar:release:{release}:{digest}"


def published_for(
    rules: list[dict[str, Any]], seen: dict[str, str], generated: str
) -> str:
    """Newest first-seen date among the release's rules.

    So an item resurfaces when a removal is announced for that release, and
    stays put when the crawl merely finds more integrations using one.
    """
    return max((seen.get(rule["id"], generated) for rule in rules), default=generated)


def _message_html(message: str) -> str:
    """Rule messages are quoted from prose that marks symbols with backticks."""
    text = html.escape(message.strip())
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Messages are sentences from a blog post or from core, and not all of them
    # were written to be followed by another one.
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _rule_list(rules: list[dict[str, Any]]) -> str:
    items = []
    for rule in rules:
        label = html.escape(rule_label(rule))
        message = _message_html(rule.get("message") or "")
        source = rule.get("source_url") or rule.get("source") or ""
        # A rule's source can be a bare "file.py:418", which is not a link.
        if str(source).startswith("http"):
            where = f' <a href="{html.escape(source)}">source</a>'
        elif source:
            where = f" <code>{html.escape(str(source))}</code>"
        else:
            where = ""
        hit = rule.get("repos_hit") or 0
        used = f" Used by {hit} {plural(hit, 'integration')}." if hit else ""
        items.append(f"<li><strong>{label}</strong>: {message}{used}{where}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _table(integrations: list[dict[str, Any]], release: str) -> str:
    ranked = sorted(
        integrations,
        key=lambda entry: (-(entry.get("stargazers_count") or 0), entry["full_name"]),
    )
    rows = []
    for entry in ranked[:MAX_TABLE_ROWS]:
        hits = [f for f in entry["findings"] if f["breaks_in"] == release]
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(entry["repo_url"])}">'
            f"{html.escape(entry['full_name'])}</a></td>"
            f"<td>{html.escape(entry['domain'])}</td>"
            f"<td>{html.escape(entry['version'])}</td>"
            f"<td>{entry.get('stargazers_count') or 0}</td>"
            f"<td>{len(hits)}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Repository</th><th>Domain</th><th>Version</th>"
        "<th>Stars</th><th>Findings</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )
    rest = len(ranked) - MAX_TABLE_ROWS
    link = f"and {rest} more on the board" if rest > 0 else "See it on the board"
    return table + f'<p><a href="{anchor_for(release)}">{link}</a></p>'


def describe(
    release: str, rules: list[dict[str, Any]], integrations: list[dict[str, Any]]
) -> str:
    """The item body: what this release removes, and who still uses it."""
    count = len(integrations)
    lead = (
        f"<p>Home Assistant {html.escape(release)} removes "
        f"{len(rules)} {plural(len(rules), 'API')}. "
        f"{count} HACS custom {plural(count, 'integration')} in the catalogue "
        f"still {plural(count, 'uses', 'use')} "
        f"{plural(len(rules), 'it', 'them')}.</p>"
    )
    heading = (
        # Ranked by stars, so say stars: "most used" would be a claim the
        # crawl cannot make.
        f"<h3>Most starred of the {count} affected</h3>"
        if count > MAX_TABLE_ROWS
        else "<h3>Affected integrations</h3>"
    )
    return (
        lead
        + "<h3>What is being removed</h3>"
        + _rule_list(rules)
        + heading
        + _table(integrations, release)
    )


def group_by_release(
    payload: dict[str, Any],
) -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """``release -> (rules, integrations)`` for every release with a finding."""
    rules_by_id = {rule["id"]: rule for rule in payload.get("rules", [])}
    # Keyed by id rather than searched with ``in``: the biggest release holds
    # 554 integrations, and ``in`` on a list of dicts compares every field of
    # every one of them.
    rules: dict[str, dict[str, dict[str, Any]]] = {}
    entries: dict[str, dict[str, dict[str, Any]]] = {}
    for integration in payload.get("integrations", []):
        for finding in integration.get("findings", []):
            release = finding["breaks_in"]
            rules.setdefault(release, {})
            entries.setdefault(release, {})[integration["full_name"]] = integration
            rule = rules_by_id.get(finding["rule_id"])
            if rule is not None:
                rules[release][rule["id"]] = rule

    grouped = {}
    for release, by_name in entries.items():
        by_id = rules[release]
        grouped[release] = (
            [by_id[rule_id] for rule_id in sorted(by_id)],
            list(by_name.values()),
        )
    return grouped


def build(payload: dict[str, Any], seen: dict[str, str]) -> str:
    """Render the feed for a built index payload."""
    generated = payload.get("generated_utc", "")
    grouped = group_by_release(payload)
    releases = sorted(
        grouped,
        key=lambda release: (
            published_for(grouped[release][0], seen, generated),
            # Every rule shares a date on the first run, so fall back to the
            # soonest deadline rather than an arbitrary string sort.
            [-part for part in parse_version(release)],
        ),
        reverse=True,
    )[:MAX_ITEMS]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        # Readers ignore this; it is what stops a browser showing raw XML.
        f'<?xml-stylesheet type="text/xsl" href="{STYLESHEET}"?>',
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
    for release in releases:
        rules, integrations = grouped[release]
        lines += [
            "    <item>",
            f"      <title>{escape(title_for(release))}</title>",
            f"      <link>{escape(anchor_for(release))}</link>",
            f'      <guid isPermaLink="false">{escape(guid_for(release, rules))}</guid>',
            f"      <pubDate>{_rfc822(published_for(rules, seen, generated))}</pubDate>",
            f"      <category>{escape(release)}</category>",
            f"      <description>{_cdata(describe(release, rules, integrations))}</description>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)
