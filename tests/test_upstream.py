"""Choosing which existing issue, if any, is worth linking someone to.

Searching a deprecated symbol also matches tracebacks pasted into unrelated
bug reports. Measured on real repositories: "Bug: Everything is unavailable"
came back for a symbol search purely because the traceback contained it, so a
raw search hit is not evidence on its own.
"""

from __future__ import annotations

import pytest

from tools.rules_engine import rule_search_term, search_term
from tools.upstream import annotate, relevance


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
    named = relevance("async_get_device is deprecated", "async_get_device")
    generic = relevance("Upcoming breaking changes", "async_get_device")
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
    assert annotate(records, rules) == 1
    assert asked == ["setup_scanner"]
    assert records["a/one"]["upstream"]["symbol"] == "setup_scanner"


def test_a_repository_with_nothing_left_loses_its_upstream_fact(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    records = {"a/one": {"findings": [], "upstream": {"symbol": "setup_scanner"}}}
    assert annotate(records, {}) == 0
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
    assert annotate(records, rules) == 1
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
    finding = [{"rule_id": "soon", "breaks_in": "2027.9"}]
    records = {
        "a/fresh": {"findings": finding, "upstream": {"checked_utc": "2026-09-16T00:00:00Z"}},
        "b/stale": {"findings": finding, "upstream": {"checked_utc": "2026-01-01T00:00:00Z"}},
        "c/never": {"findings": finding},
    }
    assert annotate(records, {"soon": {"symbol": "setup_scanner"}}, limit=2) == 2
    assert asked == ["c/never", "b/stale"]


def test_a_fact_records_when_it_was_checked(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(
        "tools.upstream.look_up",
        lambda *a, **k: {"archived": False, "issues_enabled": True},
    )
    monkeypatch.setattr("tools.upstream.utc_now_iso", lambda: "2026-09-17T12:00:00Z")
    records = {"a/one": {"findings": [{"rule_id": "soon", "breaks_in": "2027.9"}]}}
    annotate(records, {"soon": {"symbol": "setup_scanner"}})
    assert records["a/one"]["upstream"]["checked_utc"] == "2026-09-17T12:00:00Z"


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
