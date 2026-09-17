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

#: GitHub's own cap on an issue title, so a fact stores the whole of one. A
#: shorter cut here is a cut in what the relevance score reads, and an issue
#: that names the symbol late in a long title is a report, not a near miss.
TITLE_LIMIT = 256

#: How long a recorded fact is trusted before the repository is asked again.
#: An issue gets closed, renamed, or opened after the crawl last looked.
FACT_MAX_AGE_DAYS = 7

#: How many repositories one run asks about. At SEARCH_INTERVAL apart this is
#: about a quarter of an hour of the crawl job's ninety minutes, and up to two
#: REST calls each against the token's hourly budget.
LOOKUP_LIMIT = 400


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
        "title": (item.get("title") or "")[:TITLE_LIMIT],
        "reactions": (item.get("reactions") or {}).get("total_count", 0),
    }


def _rank(
    report: dict[str, Any] | None, term: str, *, current_version: str
) -> tuple[int, bool]:
    """How much this one looks like the report, or nothing at all.

    Over the title as :func:`_report` stores it, which is the whole one, so a
    hit and the fact made from it are never scored differently. An open issue
    wins a tie: it is the one worth adding a reaction to.
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


def _owner_repo(api_url: str) -> str:
    """The ``owner/name`` an API url points at."""
    return "/".join(api_url.rstrip("/").split("/")[-2:])


def confirm_report(
    full_name: str,
    report: dict[str, Any],
    term: str,
    *,
    current_version: str,
    token: str,
    canonical: str = "",
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
    where = _owner_repo(item.get("repository_url") or "")
    if item.get("number") != number or (where and where != (canonical or full_name)):
        # A transferred issue answers from the repository it went to, so it is
        # that repository's report now and linking it here sends people
        # somewhere the integration is not. A rename answers from the new name,
        # which is where ``canonical`` comes from: the repository lookup this
        # run already made followed the same redirect.
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
        # The name it answers under, which is the new one when the repository
        # has been renamed since the catalogue listed it. Not part of the fact
        # that gets stored; :func:`look_up` takes it out again.
        "canonical": data.get("full_name") or full_name,
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
    canonical = facts.pop("canonical", full_name)

    def still_open(report: dict[str, Any]) -> dict[str, Any] | None:
        return _confirmed(
            full_name,
            report,
            term,
            current_version=current_version,
            token=token,
            canonical=canonical,
        )

    if known and not facts["issues_enabled"] and not facts["archived"]:
        # Turning issues off hides the existing ones, and the API answers 404
        # or 410 for them, which is what drops the link. Saying "nowhere to
        # report it" while the report is still there to read would be worse.
        current = still_open(known)
        if current:
            facts["report"] = current
    if facts["issues_enabled"] and not facts["archived"]:
        try:
            # Under the name GitHub answers to now: a `repo:` qualifier naming
            # a repository that has been renamed is a 422, not an empty
            # answer, so a renamed one could never pick up a report.
            report = find_report(
                canonical, term, current_version=current_version, token=token
            )
        except (urllib.error.HTTPError, OSError) as err:
            # The repository itself answered, and that answer is worth
            # recording whatever the search did: a run that drops it here puts
            # last week's "archived, nothing is coming" back in front of
            # everybody for another week on evidence it already had. The
            # lookup still counts, so a repository whose search keeps failing
            # waits its turn like the rest rather than sorting to the front of
            # every run for good.
            LOGGER.debug("search failed for %s: %s", full_name, err)
            report = known
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
            current = still_open(known)
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


def upstream_still_applies(
    upstream: dict[str, Any],
    findings: list[dict[str, Any]],
    rules_by_id: dict[str, Any],
) -> bool:
    """Whether a recorded upstream fact is still about what a repository has.

    The fact is the repository's own issue about one deprecated symbol. It
    stays true while the repository still uses that symbol, which survives the
    rule being re-dated, renamed, or overtaken by one that breaks sooner.
    """
    symbol = upstream.get("symbol")
    return bool(symbol) and any(
        rule_search_term(rules_by_id.get(f.get("rule_id")) or {}) == symbol
        for f in findings
    )


def _wanted(record: dict[str, Any], rules_by_id: dict[str, Any]) -> str:
    """What to search this repository for: the term its soonest break asks.

    A report already found is the exception. It was filed under the term it
    was found with, and the scan keeps such a fact through a rule being
    overtaken by one that breaks sooner. Re-aiming it here would undo that
    within the week and trade a link somebody can open for a search that may
    well answer nothing.
    """
    findings = record.get("findings") or []
    if not findings:
        return ""
    fact = record.get("upstream") or {}
    if fact.get("report") and upstream_still_applies(fact, findings, rules_by_id):
        return str(fact["symbol"])
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
    limit: int = LOOKUP_LIMIT,
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

    # A fact is the repository's own issue about a finding, so a record the
    # scan pruned back to none has nothing left for it to be about. That is
    # free to decide and a run that spends its budget first would otherwise
    # never reach the ones at the back of the queue.
    for record in records.values():
        if not record.get("findings"):
            record.pop("upstream", None)

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
            continue
        fact = record.get("upstream") or {}
        if fact.get("symbol") == term and (fact.get("checked_utc") or "") > stale_before:
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
            gone = isinstance(err, urllib.error.HTTPError) and err.code in (404, 410)
            # A repository that answers 404 is deleted, private or gone
            # somewhere the crawl cannot follow. Its findings stay in the index
            # on purpose, but nothing on file about its issue tracker is
            # evidence any more, so it is dropped rather than republished
            # every week off the back of a failure. The two facts that say
            # there is nowhere to report it survive, because a repository
            # going away does not undo either and the card would otherwise
            # offer a search on a URL that 404s as well.
            if gone:
                carried = {}
                if fact.get("archived"):
                    carried["archived"] = True
                if fact.get("issues_enabled") is False:
                    carried["issues_enabled"] = False
            else:
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
