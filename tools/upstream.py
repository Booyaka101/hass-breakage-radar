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
from datetime import UTC, datetime, timedelta
from typing import Any

from tools.common import LOGGER, utc_now_iso
from tools.rules_engine import parse_version, rule_search_term

API = "https://api.github.com"

#: The search API allows 30 requests a minute, authenticated or not.
SEARCH_INTERVAL = 2.1

#: Words that suggest an issue is about a scheduled removal.
DEPRECATION_WORDS = re.compile(r"deprecat|removal|removed|breaking change", re.I)

#: A core release named in a title, which is how a lot of these reports are
#: worded. Only a release core has not shipped yet counts: a title naming one
#: users are already running is a bug in that release, not an answer to a
#: removal still in the future.
RELEASE_MENTION = re.compile(r"\b20\d\d\.\d+\b")

#: How long a recorded fact is trusted before the repository is asked again.
#: An issue gets closed, renamed, or opened after the crawl last looked.
FACT_MAX_AGE_DAYS = 7


class SearchExhausted(RuntimeError):
    """The search rate limit is spent; stop looking things up this run."""


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _throttled(err: urllib.error.HTTPError) -> bool:
    """Whether a 403 is the API saying "later" rather than "not this one".

    GitHub answers 403 both for a spent budget and for a repository it has
    blocked. The headers say which on a primary limit; a secondary one can
    arrive with neither header, and says so in the body instead.
    """
    if err.headers.get("retry-after") is not None:
        return True
    if err.headers.get("x-ratelimit-remaining") == "0":
        return True
    try:
        return "rate limit" in err.read(2000).decode("utf-8", "replace").lower()
    except OSError:
        return False


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
        # Treating a blocked repository as a spent budget ends the refresh on
        # every run from then on, so the two 403s are told apart.
        if err.code == 429 or (err.code == 403 and _throttled(err)):
            raise SearchExhausted(f"{path}: HTTP {err.code}") from err
        raise


def relevance(title: str, term: str, *, current_version: str) -> int:
    """How much an issue title looks like it is about this deprecation.

    ``current_version`` is the release core is building, so a title naming it
    is about something nobody is running yet.
    """
    score = 0
    if term and term.lower() in title.lower():
        score += 2
    if DEPRECATION_WORDS.search(title):
        score += 1
    elif any(
        parse_version(release) >= parse_version(current_version)
        for release in RELEASE_MENTION.findall(title)
    ):
        score += 1
    return score


def _report(item: dict[str, Any]) -> dict[str, Any]:
    """The part of an issue the board and a Repairs notice show."""
    return {
        "number": item.get("number"),
        "url": item.get("html_url", ""),
        "state": item.get("state", ""),
        "title": (item.get("title") or "")[:140],
        "reactions": (item.get("reactions") or {}).get("total_count", 0),
    }


def _rank(
    report: dict[str, Any] | None, term: str, *, current_version: str
) -> tuple[int, bool]:
    """Where :func:`find_report` would have put this one, or nothing at all."""
    if not report:
        return (0, False)
    return (
        relevance(report.get("title", ""), term, current_version=current_version),
        report.get("state") == "open",
    )


def find_report(
    full_name: str, term: str, *, current_version: str, token: str
) -> dict[str, Any] | None:
    """The most relevant existing issue matching ``term``, or None.

    Open issues win over closed ones at equal relevance, because an open
    report is the one worth adding a reaction to.
    """
    if not term:
        return None
    payload = _api(
        "/search/issues",
        token=token,
        params={"q": f'repo:{full_name} is:issue "{term}"', "per_page": "10"},
    )
    best = None
    for item in payload.get("items", []):
        score = relevance(
            item.get("title", ""), term, current_version=current_version
        )
        if score <= 0:
            continue                       # matched the body only; not evidence
        rank = (score, item.get("state") == "open")
        if best is None or rank > best[0]:
            best = (rank, _report(item))
    return best[1] if best else None


def confirm_report(
    full_name: str,
    report: dict[str, Any],
    term: str,
    *,
    current_version: str,
    token: str,
) -> dict[str, Any] | None:
    """The report already on file, as the repository has it now, or None.

    A search answers with its own top ten ranked its own way, so a known issue
    falls out of the answer without anything having happened to it. Asking for
    it by number is what tells that apart from an issue that is gone, and it
    picks up a retitle or a close on the way.
    """
    number = report.get("number")
    if not number:
        return None
    try:
        item = _api(f"/repos/{full_name}/issues/{number}", token=token)
    except urllib.error.HTTPError as err:
        if err.code in (404, 410):
            return None
        raise
    if relevance(item.get("title", ""), term, current_version=current_version) <= 0:
        return None
    return _report(item)


def repo_facts(full_name: str, *, token: str) -> dict[str, Any]:
    """Whether the repository still accepts reports at all."""
    data = _api(f"/repos/{full_name}", token=token)
    return {
        "archived": bool(data.get("archived")),
        "issues_enabled": bool(data.get("has_issues")),
    }


def look_up(
    full_name: str,
    term: str,
    *,
    current_version: str,
    known: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Repository facts plus any existing report. Never raises except when the
    rate limit is spent, which the caller uses to stop early.

    ``known`` is the report this repository was already on file for. It is
    asked about by number whenever the search comes back with nothing better,
    which is both how a dropped issue is told from a deleted one and how a good
    link survives a run where only a weaker hit came back.
    """
    token = token or _token()
    if not token:
        return {}
    facts = repo_facts(full_name, token=token)
    if facts["issues_enabled"] and not facts["archived"]:
        report = find_report(
            full_name, term, current_version=current_version, token=token
        )
        time.sleep(SEARCH_INTERVAL)
        found = _rank(report, term, current_version=current_version)
        if known and _rank(known, term, current_version=current_version) > found:
            current = confirm_report(
                full_name, known, term, current_version=current_version, token=token
            )
            if current and _rank(current, term, current_version=current_version) >= found:
                report = current
        if report:
            facts["report"] = report
    return facts


def _staleness(item: tuple[str, Any]) -> str:
    """Sort key: oldest fact first, never-looked-up repositories before those.

    A run stops at ``limit`` lookups and there are more affected repositories
    than that, so without this the budget goes to whichever ones sort first by
    name, every single day.
    """
    return (item[1].get("upstream") or {}).get("checked_utc") or ""


def annotate(
    records: dict[str, Any],
    rules_by_id: dict[str, Any],
    *,
    current_version: str,
    limit: int = 400,
    max_age_days: int = FACT_MAX_AGE_DAYS,
) -> int:
    """Add upstream facts to scan records that have findings.

    Offer every affected repository, not just the ones a slice rescanned: a
    repository that cuts no release is otherwise never looked up again and its
    fact stays published however wrong it has gone. A fact younger than
    ``max_age_days`` that is still filed under the term its rule asks for is
    left alone, so a run spends its lookups on the oldest ones and on the rules
    that have been re-aimed since.

    Returns how many repositories were asked, failures included: the budget is
    requests, not answers. Anything that fails is skipped rather than allowed
    to fail the crawl, because this is extra context, not the product.
    """
    token = _token()
    if not token:
        LOGGER.info("no GITHUB_TOKEN; skipping upstream issue lookup")
        return 0

    stale_before = (
        datetime.now(UTC) - timedelta(days=max_age_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    asked = 0
    for full_name, record in sorted(records.items(), key=_staleness):
        if asked >= limit:
            break
        findings = record.get("findings") or []
        if not findings:
            record.pop("upstream", None)
            continue
        earliest = min(findings, key=lambda f: parse_version(f.get("breaks_in", "")))
        rule = rules_by_id.get(earliest.get("rule_id"), {})
        term = rule_search_term(rule)
        fact = record.get("upstream") or {}
        if fact.get("symbol") == term and fact.get("checked_utc", "") > stale_before:
            continue
        asked += 1
        on_file = fact.get("report") if fact.get("symbol") == term else None
        try:
            facts = look_up(
                full_name,
                term,
                current_version=current_version,
                known=on_file,
                token=token,
            )
        except SearchExhausted as err:
            LOGGER.warning("upstream lookup stopped early: %s", err)
            break
        except Exception as err:  # noqa: BLE001 - context is optional
            LOGGER.debug("upstream lookup failed for %s: %s", full_name, err)
            # Record the attempt and nothing else, so the repository comes
            # round again with the rest of them rather than sorting to the
            # front of every run's budget for good. A fact with no answer in it
            # reads exactly as no fact at all.
            carried = dict(fact)
            if not on_file:
                # Whatever was on file was found for a term this rule no longer
                # asks for. The rest of it is still true about the repository.
                carried.pop("report", None)
            record["upstream"] = {
                **carried,
                "symbol": term,
                "checked_utc": utc_now_iso(),
            }
            continue
        if not facts:
            continue
        facts["symbol"] = term
        facts["checked_utc"] = utc_now_iso()
        record["upstream"] = facts
    return asked
