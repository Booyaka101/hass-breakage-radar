"""Choosing which existing issue, if any, is worth linking someone to.

Searching a deprecated symbol also matches tracebacks pasted into unrelated
bug reports. Measured on real repositories: "Bug: Everything is unavailable"
came back for a symbol search purely because the traceback contained it, so a
raw search hit is not evidence on its own.
"""

from __future__ import annotations

import pytest

from tools.upstream import relevance, search_term


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
    ],
)
def test_only_titles_that_look_like_the_deprecation_count(title, symbol, wanted):
    assert (relevance(title, symbol) > 0) is wanted


def test_a_symbol_in_the_title_outranks_a_generic_deprecation_notice():
    named = relevance("async_get_device is deprecated", "async_get_device")
    generic = relevance("Upcoming breaking changes", "async_get_device")
    assert named > generic > 0
