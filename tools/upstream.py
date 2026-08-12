"""Looks up what an integration's own repository already says about a finding.

A user told "this breaks" needs to know whether it is already reported before
doing anything. Asking every user's Home Assistant to search GitHub would need
a token from each of them and would hit the 30 requests per minute search
limit immediately, so the crawler does it once and publishes the answer.

Only reports that look like they are about the deprecation are returned. A
search for the symbol also matches tracebacks pasted into unrelated bug
reports, and linking someone to "Bug: everything is unavailable" as though it
were the report would be worse than saying nothing.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from tools.common import LOGGER

API = "https://api.github.com"

#: The search API allows 30 requests a minute, authenticated or not.
SEARCH_INTERVAL = 2.1

#: Words that suggest an issue is about a scheduled removal.
DEPRECATION_WORDS = re.compile(
    r"deprecat|removal|removed|breaking change|\b20\d\d\.\d+\b", re.I
)


class SearchExhausted(RuntimeError):
    """The search rate limit is spent; stop looking things up this run."""


def search_term(symbol: str) -> str:
    """The searchable part of a rule symbol.

    ``DeviceRegistry.async_get_device`` and
    ``async_import_statistics(missing metadata)`` both reduce to the bare
    function name, which is what someone would paste into an issue.
    """
    return symbol.split("(")[0].strip().split(".")[-1].strip()


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _api(path: str, *, token: str, params: dict[str, str] | None = None) -> Any:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "breakage-radar",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (403, 429):
            raise SearchExhausted(f"{path}: HTTP {err.code}") from err
        raise


def relevance(title: str, symbol: str) -> int:
    """How much an issue title looks like it is about this deprecation."""
    score = 0
    if symbol and symbol.lower() in title.lower():
        score += 2
    if DEPRECATION_WORDS.search(title):
        score += 1
    return score


def find_report(full_name: str, symbol: str, *, token: str) -> dict[str, Any] | None:
    """The most relevant existing issue about ``symbol``, or None.

    Open issues win over closed ones at equal relevance, because an open
    report is the one worth adding a reaction to.
    """
    term = search_term(symbol)
    if not term:
        return None
    payload = _api(
        "/search/issues",
        token=token,
        params={"q": f'repo:{full_name} is:issue "{term}"', "per_page": "10"},
    )
    best = None
    for item in payload.get("items", []):
        score = relevance(item.get("title", ""), term)
        if score <= 0:
            continue                       # matched the body only; not evidence
        rank = (score, item.get("state") == "open")
        if best is None or rank > best[0]:
            best = (
                rank,
                {
                    "number": item.get("number"),
                    "url": item.get("html_url", ""),
                    "state": item.get("state", ""),
                    "title": (item.get("title") or "")[:140],
                    "reactions": (item.get("reactions") or {}).get("total_count", 0),
                },
            )
    return best[1] if best else None


def repo_facts(full_name: str, *, token: str) -> dict[str, Any]:
    """Whether the repository still accepts reports at all."""
    data = _api(f"/repos/{full_name}", token=token)
    return {
        "archived": bool(data.get("archived")),
        "issues_enabled": bool(data.get("has_issues")),
    }


def look_up(full_name: str, symbol: str, *, token: str | None = None) -> dict[str, Any]:
    """Repository facts plus any existing report. Never raises except when the
    rate limit is spent, which the caller uses to stop early."""
    token = token or _token()
    if not token:
        return {}
    facts = repo_facts(full_name, token=token)
    if facts["issues_enabled"] and not facts["archived"]:
        report = find_report(full_name, symbol, token=token)
        time.sleep(SEARCH_INTERVAL)
        if report:
            facts["report"] = report
    return facts


def annotate(
    records: dict[str, Any], rules_by_id: dict[str, Any], *, limit: int = 400
) -> int:
    """Add upstream facts to scan records that have findings.

    Returns how many were looked up. Anything that fails is skipped rather
    than allowed to fail the crawl: this is extra context, not the product.
    """
    token = _token()
    if not token:
        LOGGER.info("no GITHUB_TOKEN; skipping upstream issue lookup")
        return 0

    done = 0
    for full_name, record in records.items():
        if done >= limit:
            break
        findings = record.get("findings") or []
        if not findings:
            record.pop("upstream", None)
            continue
        earliest = min(findings, key=lambda f: f.get("breaks_in", ""))
        rule = rules_by_id.get(earliest.get("rule_id"), {})
        try:
            facts = look_up(full_name, rule.get("symbol", ""), token=token)
        except SearchExhausted as err:
            LOGGER.warning("upstream lookup stopped early: %s", err)
            break
        except Exception as err:  # noqa: BLE001 - context is optional
            LOGGER.debug("upstream lookup failed for %s: %s", full_name, err)
            continue
        if facts:
            facts["symbol"] = search_term(rule.get("symbol", ""))
            record["upstream"] = facts
            done += 1
    return done
