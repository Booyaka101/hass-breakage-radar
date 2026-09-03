#!/usr/bin/env python3
"""Merge prose-only deprecations into the rule set.

Home Assistant announces plenty of removals that never appear as a
``report_usage(..., breaks_in_ha_version=...)`` call in core -- the legacy
device tracker platform API being the flagship example. Two sources cover them:

1. **The developer blog index** (https://developers.home-assistant.io/blog/).
   Every post is scanned for sentences naming a Core release, e.g. *"will be
   removed in the Home Assistant 2027.5 release"*. These become **informational**
   rules: they appear on the board so a user can see the deadline, but they carry
   no matcher and never claim a repository is affected.

2. **``data/manual_rules.json``** -- hand-curated rules with real matchers, each
   traceable to one of those posts. This is where the machine-checkable version
   of a prose deprecation lives, because turning English into an AST matcher is
   a judgement call that must not be automated (see README, "How rules are
   chosen").

Reads ``data/rules.json`` (from ``tools/extract_rules.py``) and rewrites it with
the blog and manual rules merged in.

Usage::

    python tools/blog_rules.py [--rules data/rules.json] [--no-network]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import (  # noqa: E402
    DATA_DIR,
    LOGGER,
    http_get,
    read_json,
    setup_logging,
    utc_now_iso,
    write_json,
)
from tools.release import floor_from_payload  # noqa: E402
from tools.rules_engine import (  # noqa: E402
    MATCHER_TYPES,
    VERSION_RE,
    is_pending,
    normalise_version,
)

BLOG_INDEX = "https://developers.home-assistant.io/blog/"
BLOG_BASE = "https://developers.home-assistant.io"

#: Sentences that announce a removal, all seen live on the real blog.
REMOVAL_PATTERNS = [
    re.compile(
        r"(?:will be |is )?removed in (?:the )?(?:Home Assistant )?(?:Core )?(\d{4}\.\d+(?:\.\d+)?)",
        re.I,
    ),
    re.compile(
        r"will stop working in (?:the )?(?:Home Assistant )?(?:Core )?(\d{4}\.\d+(?:\.\d+)?)",
        re.I,
    ),
    re.compile(
        r"(?:supported|kept|keeps working) until (?:Home Assistant )?(?:Core )?(\d{4}\.\d+(?:\.\d+)?)",
        re.I,
    ),
    re.compile(r"Removal in (?:Home Assistant )?Core (\d{4}\.\d+(?:\.\d+)?)", re.I),
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_POST_HREF_RE = re.compile(r'href="(/blog/\d{4}/\d{2}/\d{2}/[a-z0-9\-._]+)"', re.I)


def _text(markup: str) -> str:
    """Strip HTML down to readable prose."""
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", markup)
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70] or "post"


def discover_posts(index_html: str) -> list[str]:
    """Return absolute URLs of every blog post linked from the index."""
    seen: list[str] = []
    for path in _POST_HREF_RE.findall(index_html):
        url = BLOG_BASE + path.rstrip("/") + "/"
        if url not in seen:
            seen.append(url)
    return seen


def _sentences(text: str) -> Iterable[str]:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if sentence:
            yield sentence


def extract_removals(url: str, text: str) -> list[dict[str, Any]]:
    """Find every 'removed in <release>' sentence in one post's prose."""
    title_slug = url.rstrip("/").rsplit("/", 1)[-1]
    found: dict[str, dict[str, Any]] = {}

    for sentence in _sentences(text):
        for pattern in REMOVAL_PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            version = normalise_version(match.group(1))
            if not VERSION_RE.match(version):
                continue
            key = version
            if key in found:
                continue
            trimmed = sentence if len(sentence) <= 400 else sentence[:397] + "..."
            found[key] = {
                "id": f"blog-{_slug(title_slug)}-{version}",
                "kind": "prose",
                "symbol": title_slug.replace("-", " "),
                "message": trimmed,
                "breaks_in": version,
                "source": url,
                "origin": "blog",
                "confidence": "info",
                "matchable": False,
            }
            break
    return list(found.values())


def fetch_blog_rules(
    *, index_url: str = BLOG_INDEX, limit: int = 120, pages: int = 8
) -> list[dict]:
    """Crawl the paginated blog index and return informational rules.

    Never raises: a blog outage degrades the board, it must not fail the crawl.
    """
    posts: list[str] = []
    for page in range(1, pages + 1):
        url = index_url if page == 1 else f"{BLOG_BASE}/blog/page/{page}"
        try:
            index_html = http_get(url, timeout=60).decode("utf-8", "replace")
        except Exception as err:
            LOGGER.warning("stopping blog pagination at %s: %s", url, err)
            break
        found = [p for p in discover_posts(index_html) if p not in posts]
        if not found:
            break
        posts.extend(found)

    LOGGER.info("blog index lists %d posts across up to %d pages", len(posts), pages)

    rules: list[dict[str, Any]] = []
    for url in posts[:limit]:
        try:
            body = http_get(url, timeout=60).decode("utf-8", "replace")
        except Exception as err:
            LOGGER.warning("skipping %s: %s", url, err)
            continue
        hits = extract_removals(url, _text(body))
        if hits:
            LOGGER.info(
                "%s -> %s", url, ", ".join(sorted(h["breaks_in"] for h in hits))
            )
        rules.extend(hits)
    return rules


def load_manual_rules(path: Path) -> list[dict[str, Any]]:
    """Read and validate ``data/manual_rules.json``."""
    payload = read_json(path, default=None)
    if payload is None:
        LOGGER.warning("%s not found; no manual rules merged", path)
        return []
    raw = payload.get("rules", []) if isinstance(payload, dict) else payload
    rules: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or "id" not in item or "breaks_in" not in item:
            LOGGER.warning("ignoring malformed manual rule: %r", item)
            continue
        matcher = item.get("match")
        if matcher is not None and matcher.get("type") not in MATCHER_TYPES:
            LOGGER.error(
                "manual rule %s has unknown matcher type %r; dropping the matcher",
                item["id"],
                matcher.get("type"),
            )
            item = {**item, "match": None}
        entry = dict(item)
        entry.setdefault("kind", "call")
        entry.setdefault("origin", "manual")
        entry.setdefault("confidence", "high")
        entry["matchable"] = bool(entry.get("match"))
        entry["breaks_in"] = normalise_version(entry["breaks_in"])
        rules.append(entry)
    return rules


def _what_it_matches(match: dict[str, Any]) -> str:
    """The part of a matcher that decides which code it fires on.

    ``allow_unresolved_attribute`` and ``not_awaited`` widen or narrow a
    hand-written rule around the same calls, so they do not make it a
    different rule.
    """
    keys = ("type", "names", "bases", "modules", "kwargs", "kwarg", "files", "in_class_base")
    return json.dumps({k: match[k] for k in keys if k in match}, sort_keys=True)


def merge(
    core_rules: list[dict[str, Any]],
    manual: list[dict[str, Any]],
    blog: list[dict[str, Any]],
    *,
    pending_floor: str,
) -> list[dict[str, Any]]:
    """Merge the three sources. Manual wins on id collision; blog never does.

    A manual rule also wins over a core rule that matches the same calls: the
    hand-written ``device-registry-async-get-device`` and the extracted
    ``core-call-async-get-device`` would otherwise both fire on every line,
    and one finding reported twice reads as two problems.
    """
    hand_written = {_what_it_matches(rule["match"]) for rule in manual if rule.get("match")}
    merged: dict[str, dict[str, Any]] = {}
    for rule in core_rules:
        if rule.get("match") and _what_it_matches(rule["match"]) in hand_written:
            LOGGER.info("%s is covered by a hand-written rule; dropping it", rule["id"])
            continue
        merged[rule["id"]] = rule

    for rule in manual:
        merged[rule["id"]] = rule

    matchable_symbols = {
        name
        for rule in merged.values()
        if rule.get("match")
        for name in (
            rule["match"].get("names", [])
            + rule["match"].get("bases", [])
            + rule["match"].get("files", [])
        )
    }

    for rule in blog:
        if rule["id"] in merged:
            continue
        # Suppress a prose rule that only restates a release we already match on
        # with a real matcher, to keep the board free of duplicates.
        if any(symbol in rule["message"] for symbol in matchable_symbols):
            rule = {**rule, "duplicate_of_matchable_release": True}
        merged[rule["id"]] = rule

    for rule in merged.values():
        rule["expired"] = not is_pending(rule["breaks_in"], pending_floor)

    return sorted(merged.values(), key=lambda r: (r["breaks_in"], r["id"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rules", type=Path, default=DATA_DIR / "rules.json")
    parser.add_argument("--manual", type=Path, default=DATA_DIR / "manual_rules.json")
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="merge manual rules only; do not crawl the developer blog",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    payload = read_json(args.rules, default=None)
    if payload is None:
        LOGGER.error(
            "%s not found -- run `python tools/extract_rules.py` first", args.rules
        )
        return 2

    floor, _source = floor_from_payload(payload)
    core_rules = [r for r in payload.get("rules", []) if r.get("origin") != "blog"]
    core_rules = [r for r in core_rules if r.get("origin") != "manual"]

    manual = load_manual_rules(args.manual)
    blog = [] if args.no_network else fetch_blog_rules()

    merged = merge(core_rules, manual, blog, pending_floor=floor)

    payload["rules"] = merged
    payload["blog_merged_utc"] = utc_now_iso()
    future = [r for r in merged if not r["expired"]]
    payload["counts"] = {
        **payload.get("counts", {}),
        "total": len(merged),
        "future": len(future),
        "matchable_future": sum(1 for r in future if r.get("matchable")),
        "from_core_ast": sum(1 for r in merged if r.get("origin") == "core-ast"),
        "from_manual": sum(1 for r in merged if r.get("origin") == "manual"),
        "from_blog": sum(1 for r in merged if r.get("origin") == "blog"),
    }
    write_json(args.rules, payload)

    LOGGER.info(
        "wrote %s: %d rules (%d future, %d matchable) "
        "[core-ast %d, manual %d, blog %d]",
        args.rules,
        len(merged),
        len(future),
        payload["counts"]["matchable_future"],
        payload["counts"]["from_core_ast"],
        payload["counts"]["from_manual"],
        payload["counts"]["from_blog"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
