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
from collections.abc import Callable
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
    """How much this one looks like the report, or nothing at all.

    Over the title as :func:`_report` stores it, cut to 140 characters, which
    is also the title anyone reading the board is shown. An open issue wins a
    tie: it is the one worth adding a reaction to.
    """
    if not report:
        return (0, False)
    return (
        relevance(report.get("title", ""), term, current_version=current_version),
        report.get("state") == "open",
    )


def find_report(
    full_name: str, term: str, *, current_version: str, token: str
) -> dict[str, Any] | None:
    """The most relevant existing issue matching ``term``, or None."""
    if not term:
        return None
    payload = _api(
        "/search/issues",
        token=token,
        params={"q": f'repo:{full_name} is:issue "{term}"', "per_page": "10"},
    )
    best = None
    for item in payload.get("items", []):
        candidate = _report(item)
        rank = _rank(candidate, term, current_version=current_version)
        if rank[0] <= 0:
            continue                       # matched the body only; not evidence
        if best is None or rank > best[0]:
            best = (rank, candidate)
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
    if not (item.get("repository_url") or "").lower().endswith(
        f"/repos/{full_name.lower()}"
    ):
        # A transferred issue answers from wherever it went, with that
        # repository's numbering, and recording that number here would have
        # the next run asking for an unrelated issue of ours by the same one.
        return None
    current = _report(item)
    if _rank(current, term, current_version=current_version)[0] <= 0:
        return None
    return current


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

    ``known`` is the report this repository was already on file for. Whenever
    the search does not come back with that one, it is asked about by number:
    the search ranks its own way over ten hits, so the issue drops out of the
    answer without anything having happened to it, and replacing a real report
    with an unrelated hit sends everybody off to file a duplicate.
    """
    token = token or _token()
    if not token:
        return {}
    facts = repo_facts(full_name, token=token)
    if known and not facts["issues_enabled"] and not facts["archived"]:
        # Turning issues off hides the existing ones, and the API answers 404
        # or 410 for them, which is what drops the link. Saying "nowhere to
        # report it" while the report is still there to read would be worse.
        current = _confirmed(
            full_name, known, term, current_version=current_version, token=token
        )
        if current:
            facts["report"] = current
    if facts["issues_enabled"] and not facts["archived"]:
        try:
            report = find_report(
                full_name, term, current_version=current_version, token=token
            )
        finally:
            # Spacing the searches, not their answers. A search that 502s costs
            # the same against the secondary rate limit as one that works, and
            # a caller that logs the failure and moves on would otherwise fire
            # the whole budget of them back to back.
            time.sleep(SEARCH_INTERVAL)
        found = _rank(report, term, current_version=current_version)
        if (
            known
            and (report or {}).get("number") != known.get("number")
            and _rank(known, term, current_version=current_version) >= found
        ):
            current = _confirmed(
                full_name, known, term, current_version=current_version, token=token
            )
            if current and _rank(current, term, current_version=current_version) >= found:
                report = current
        if report:
            facts["report"] = report
    return facts


def _confirmed(
    full_name: str, known: dict[str, Any], term: str, **kwargs: Any
) -> dict[str, Any] | None:
    """:func:`confirm_report`, with the known report standing in on a failure.

    Dropping a good link because the one extra call came back 502 costs a week
    of "nobody has reported this" on a repository where somebody has.
    """
    try:
        return confirm_report(full_name, known, term, **kwargs)
    except (urllib.error.HTTPError, OSError) as err:
        LOGGER.debug("could not confirm %s #%s: %s", full_name, known.get("number"), err)
        return known


def _wanted(record: dict[str, Any], rules_by_id: dict[str, Any]) -> str:
    """What to search this repository for: the term its soonest break asks."""
    findings = record.get("findings") or []
    if not findings:
        return ""
    earliest = min(findings, key=lambda f: parse_version(f.get("breaks_in", "")))
    return rule_search_term(rules_by_id.get(earliest.get("rule_id"), {}))


def _staleness(item: tuple[str, Any, str]) -> tuple[bool, str]:
    """Sort key: never looked up first, then wrong facts, then the oldest.

    A run stops at ``limit`` lookups and there are more affected repositories
    than that, so without this the budget goes to whichever ones sort first by
    name, every single day. A fact filed under a term its rule no longer asks
    for shares the front of the queue: it is a link found for a search this
    rule no longer makes, which is worse than an old one and would otherwise
    wait behind every fact older than it.
    """
    fact = item[1].get("upstream") or {}
    return (fact.get("symbol") == item[2], fact.get("checked_utc") or "")


def annotate(
    records: dict[str, Any],
    rules_by_id: dict[str, Any],
    *,
    current_version: str,
    limit: int = 400,
    max_age_days: int = FACT_MAX_AGE_DAYS,
    checkpoint: Callable[[], None] | None = None,
) -> int:
    """Add upstream facts to scan records that have findings.

    Offer every affected repository, not just the ones a slice rescanned: a
    repository that cuts no release is otherwise never looked up again and its
    fact stays published however wrong it has gone. A fact younger than
    ``max_age_days`` that is still filed under the term its rule asks for is
    left alone, so a run spends its lookups on the rules that have been re-aimed
    since and then on the oldest facts.

    Returns how many repositories were asked, failures included: the budget is
    asking, not answering. Anything that fails is skipped rather than allowed
    to fail the crawl, because this is extra context, not the product.

    ``checkpoint`` is called every 25 lookups. A full run of them takes about
    a quarter of an hour, and a cancelled job that saved none of it has spent
    the rate limit for nothing.
    """
    token = _token()
    if not token:
        LOGGER.info("no GITHUB_TOKEN; skipping upstream issue lookup")
        return 0

    stale_before = (
        datetime.now(UTC) - timedelta(days=max_age_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    asked = 0
    queue = sorted(
        ((name, record, _wanted(record, rules_by_id)) for name, record in records.items()),
        key=_staleness,
    )
    for full_name, record, term in queue:
        if asked >= limit:
            break
        if not record.get("findings"):
            record.pop("upstream", None)
            continue
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
            # Record the attempt, so the repository comes round again with the
            # rest of them rather than sorting to the front of every run's
            # budget for good.
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
        else:
            if facts:
                facts["symbol"] = term
                facts["checked_utc"] = utc_now_iso()
                record["upstream"] = facts
        if checkpoint and asked % 25 == 0:
            checkpoint()
    return asked
