"""Choosing which existing issue, if any, is worth linking someone to.

Searching a deprecated symbol also matches tracebacks pasted into unrelated
bug reports. Measured on real repositories: "Bug: Everything is unavailable"
came back for a symbol search purely because the traceback contained it, so a
raw search hit is not evidence on its own.
"""

from __future__ import annotations

import urllib.error

import pytest

from tools.common import utc_now_iso
from tools.rules_engine import rule_search_term, search_term
from tools.upstream import SearchExhausted, annotate, relevance, repo_facts

#: The release core is building, which is what the crawler passes.
NOW = "2026.10"

SOON = {"soon": {"symbol": "setup_scanner"}}
FINDING = [{"rule_id": "soon", "breaks_in": "2027.9"}]


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("DeviceRegistry.async_get_device", "async_get_device"),
        ("async_import_statistics(missing metadata)", "async_import_statistics"),
        ("verify_domain_control", "verify_domain_control"),
        ("", ""),
    ],
)
def test_search_term_reduces_a_symbol_to_what_someone_would_paste(symbol, expected):
    assert search_term(symbol) == expected


@pytest.mark.parametrize(
    "title,symbol,wanted",
    [
        # Real titles, from repositories the crawler has scanned.
        ("Deprecated argument hass was passed to async_extract_config_entry_ids",
         "async_extract_config_entry_ids", True),
        ("The deprecated argument hass was passed to verify_domain_control",
         "verify_domain_control", True),
        ("Scheduled API removals: statistics metadata (2026.11)",
         "async_import_statistics", True),
        ("Specify mean_type when calling async_import_statistics",
         "async_import_statistics", True),
        # The false match that made this gate necessary.
        ("Bug: Everything is unavailable", "async_extract_entity_ids", False),
        ("Add support for the new sensor", "async_get_device", False),
        # A release on its own is not the word: these were both published.
        ("Not working on 2021.12", "devices", False),
        ("Errors with 2022.11.x", "devices", False),
    ],
)
def test_only_titles_that_look_like_the_deprecation_count(title, symbol, wanted):
    assert (relevance(title, symbol, current_version="2026.9") > 0) is wanted


def test_a_release_still_to_come_counts_on_its_own():
    """Maintainers title these by release as often as by word: "Home Assistant
    2027.8 API changes" is the repository answering the deprecation. A release
    that has already shipped is a bug report about that release."""
    named = "setup_scanner"
    now = "2026.9"
    assert relevance("Home Assistant 2027.8 API changes", named, current_version=now) == 1
    assert relevance("Errors with 2022.11.x", named, current_version=now) == 0


def test_a_symbol_in_the_title_outranks_a_generic_deprecation_notice():
    named = relevance("async_get_device is deprecated", "async_get_device", current_version="2026.10")
    generic = relevance("Upcoming breaking changes", "async_get_device", current_version="2026.10")
    assert named > generic > 0


def test_the_finding_looked_up_is_the_one_that_breaks_soonest(monkeypatch):
    """2027.10 is after 2027.9, not before it. Compared as text it wins the
    `min` and the whole repository gets searched for the wrong symbol."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    asked: list[str] = []

    def fake_look_up(full_name, symbol, **kwargs):
        asked.append(symbol)
        return {"archived": False, "issues_enabled": True}

    monkeypatch.setattr("tools.upstream.look_up", fake_look_up)
    records = {
        "a/one": {
            "findings": [
                {"rule_id": "later", "breaks_in": "2027.10"},
                {"rule_id": "soon", "breaks_in": "2027.9"},
            ]
        }
    }
    rules = {"soon": {"symbol": "setup_scanner"}, "later": {"symbol": "async_get_device"}}
    assert annotate(records, rules, current_version=NOW) == 1
    assert asked == ["setup_scanner"]
    assert records["a/one"]["upstream"]["symbol"] == "setup_scanner"


def test_a_repository_with_nothing_left_loses_its_upstream_fact(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    records = {"a/one": {"findings": [], "upstream": {"symbol": "setup_scanner"}}}
    assert annotate(records, {}, current_version=NOW) == 0
    assert "upstream" not in records["a/one"]


def test_a_rule_can_name_the_term_its_repositories_are_searched_for(monkeypatch):
    """`devices` on its own found 14 unrelated device bugs and 4 real reports."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    asked: list[str] = []

    def fake_look_up(full_name, term, **kwargs):
        asked.append(term)
        return {"archived": False, "issues_enabled": True}

    monkeypatch.setattr("tools.upstream.look_up", fake_look_up)
    records = {"a/one": {"findings": [{"rule_id": "mapping", "breaks_in": "2027.9"}]}}
    rules = {
        "mapping": {
            "symbol": "DeviceRegistry.devices",
            "search": "device_registry.devices",
        }
    }
    assert annotate(records, rules, current_version=NOW) == 1
    assert asked == ["device_registry.devices"]
    assert records["a/one"]["upstream"]["symbol"] == "device_registry.devices"


def test_the_lookup_budget_goes_to_the_oldest_facts(monkeypatch):
    """A run looks up a few hundred of the thousands of affected repositories.
    In catalogue order, the same few hundred are the only ones ever refreshed."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    asked: list[str] = []

    def fake_look_up(full_name, term, **kwargs):
        asked.append(full_name)
        return {"archived": False, "issues_enabled": True}

    monkeypatch.setattr("tools.upstream.look_up", fake_look_up)
    records = {
        "a/fresh": {"findings": FINDING, "upstream": {"checked_utc": "2026-09-16T00:00:00Z"}},
        "b/stale": {"findings": FINDING, "upstream": {"checked_utc": "2026-01-01T00:00:00Z"}},
        "c/never": {"findings": FINDING},
    }
    assert annotate(records, SOON, current_version=NOW, limit=2) == 2
    assert asked == ["c/never", "b/stale"]


def test_a_fact_records_when_it_was_checked(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    monkeypatch.setattr("tools.upstream.utc_now_iso", lambda: "2026-09-17T12:00:00Z")
    records = {"a/one": {"findings": FINDING}}
    annotate(records, SOON, current_version=NOW)
    assert records["a/one"]["upstream"]["checked_utc"] == "2026-09-17T12:00:00Z"


def _never_called(*args, **kwargs):
    raise AssertionError("looked a repository up when its fact was still good")


def test_a_fact_that_is_still_good_costs_no_lookup(monkeypatch):
    """Every affected repository is offered on every run, not just the ones
    the slice rescanned, so freshness is what keeps a run finite."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr("tools.upstream.look_up", _never_called)
    records = {
        "a/one": {
            "findings": FINDING,
            "upstream": {"symbol": "setup_scanner", "checked_utc": utc_now_iso()},
        }
    }
    assert annotate(records, SOON, current_version=NOW) == 0


def test_a_fact_the_rule_no_longer_aims_at_is_refreshed_however_fresh(monkeypatch):
    """The 14 wrong `devices` links were all recorded yesterday. Age alone
    would have kept every one of them published."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    records = {
        "a/one": {
            "findings": [{"rule_id": "mapping", "breaks_in": "2027.9"}],
            "upstream": {"symbol": "devices", "checked_utc": utc_now_iso()},
        }
    }
    rules = {"mapping": {"symbol": "DeviceRegistry.devices", "search": "device_registry.devices"}}
    assert annotate(records, rules, current_version=NOW) == 1
    assert records["a/one"]["upstream"]["symbol"] == "device_registry.devices"


def test_a_fact_older_than_the_max_age_is_asked_again(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    fresh = {"symbol": "setup_scanner", "checked_utc": utc_now_iso()}
    records = {"a/one": {"findings": FINDING, "upstream": fresh}}
    assert annotate(records, SOON, current_version=NOW, max_age_days=0) == 1

def test_a_report_survives_a_search_that_comes_back_empty(monkeypatch):
    """The search answers with its own top ten. An issue falling out of that
    is not the issue being gone, and emptying the "already reported" column
    sends everybody off to file a duplicate."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    report = {"number": 41, "url": "u", "state": "open", "title": "setup_scanner is deprecated"}
    records = {
        "a/one": {
            "findings": FINDING,
            "upstream": {"symbol": "setup_scanner", "report": report},
        }
    }
    assert annotate(records, SOON, current_version=NOW) == 1
    assert records["a/one"]["upstream"]["report"] == report


def test_a_report_the_gate_now_rejects_is_not_carried_over(monkeypatch):
    """This is the point of the release gate: "Not working on 2021.12" was
    published as a repository's answer to a 2027 removal."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    records = {
        "a/one": {
            "findings": FINDING,
            "upstream": {
                "symbol": "setup_scanner",
                "report": {"number": 7, "title": "Not working on 2021.12"},
            },
        }
    }
    assert annotate(records, SOON, current_version=NOW) == 1
    assert "report" not in records["a/one"]["upstream"]


def test_a_failed_lookup_still_costs_the_budget(monkeypatch):
    """Every affected repository is a candidate now, and a repository renamed
    out from under the catalogue 404s. Counting answers instead of requests
    would let one bad run walk the whole 800."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    asked: list[str] = []

    def fail(full_name, term, **kwargs):
        asked.append(full_name)
        raise RuntimeError("404")

    monkeypatch.setattr("tools.upstream.look_up", fail)
    records = {n: {"findings": FINDING} for n in ("a/one", "b/two", "c/three")}
    assert annotate(records, SOON, current_version=NOW, limit=2) == 2
    assert asked == ["a/one", "b/two"]


def _http_error(code, headers):
    return urllib.error.HTTPError("https://api.github.com/x", code, "no", headers, None)


def _raises(error):
    def urlopen(request, timeout=0):
        raise error

    return urlopen


def test_a_403_with_the_budget_spent_ends_the_run(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", _raises(_http_error(403, {"x-ratelimit-remaining": "0"}))
    )
    with pytest.raises(SearchExhausted):
        repo_facts("a/one", token="x")


def test_a_403_from_a_blocked_repository_is_just_that_repository(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _raises(_http_error(403, {"x-ratelimit-remaining": "4999"})),
    )
    with pytest.raises(urllib.error.HTTPError):
        repo_facts("a/one", token="x")


def test_a_failure_stops_blocking_the_front_of_the_queue(monkeypatch):
    """403 is both "you asked too often" and "this repository is blocked". Read
    as the first, one blocked repository ends the refresh on every run, and it
    sorts to the front of them because its fact never gets a timestamp."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    calls: list[str] = []

    def blocked(full_name, term, **kwargs):
        calls.append(full_name)
        raise _http_error(403, {"x-ratelimit-remaining": "4999"})

    monkeypatch.setattr("tools.upstream.look_up", blocked)
    fact = {"symbol": "setup_scanner", "checked_utc": "2026-01-01T00:00:00Z"}
    records = {
        "a/one": {"findings": FINDING, "upstream": fact},
        "b/two": {"findings": FINDING},
    }
    assert annotate(records, SOON, current_version=NOW) == 2
    assert calls == ["b/two", "a/one"]
    assert records["a/one"]["upstream"]["checked_utc"] > "2026-01-01T00:00:00Z"


def test_a_spent_rate_limit_still_ends_the_run(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    calls: list[str] = []

    def limited(full_name, term, **kwargs):
        calls.append(full_name)
        raise SearchExhausted("search: HTTP 403")

    monkeypatch.setattr("tools.upstream.look_up", limited)
    records = {n: {"findings": FINDING} for n in ("a/one", "b/two")}
    assert annotate(records, SOON, current_version=NOW) == 1
    assert calls == ["a/one"]


def test_an_archived_repository_does_not_keep_republishing_its_report(monkeypatch):
    """Nothing searched, so there is nothing to have missed the report."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": True, "issues_enabled": True},
    )
    records = {
        "a/one": {
            "findings": FINDING,
            "upstream": {
                "symbol": "setup_scanner",
                "report": {"number": 41, "title": "setup_scanner is deprecated"},
            },
        }
    }
    assert annotate(records, SOON, current_version=NOW) == 1
    assert "report" not in records["a/one"]["upstream"]


@pytest.mark.parametrize(
    "rule,expected",
    [
        ({"symbol": "DeviceRegistry.devices", "search": "device_registry.devices"},
         "device_registry.devices"),
        ({"symbol": "DeviceRegistry.async_get_device"}, "async_get_device"),
        ({"symbol": "DeviceRegistry.devices", "search": ""}, "devices"),
        ({}, ""),
    ],
)
def test_the_search_term_is_the_override_or_the_reduced_symbol(rule, expected):
    assert rule_search_term(rule) == expected
