"""Deprecations that log a warning some releases before they are removed.

`DeviceEntry.config_entries` starts warning in 2026.10 and disappears in
2027.10. A board that shows only the removal tells a maintainer nothing about
the logs filling up next month, and one that shows only the warning tells them
to panic two years early. Both dates, on the rules that have two.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from tools.blog_rules import load_manual_rules
from tools.build_index import rule_item
from tools.feed import MAX_LABEL, rule_label, warns
from tools.rules_engine import clip, reports_before_removal
from tools.schedule import describe_report

TODAY = date(2026, 9, 17)

TWO_DATES = {
    "id": "device-entry-config-entries",
    "message": "DeviceEntry.config_entries is deprecated.",
    "source": "https://developers.home-assistant.io/blog/2026/09/15/x",
    "breaks_in": "2027.10",
    "reports_in": "2026.10",
}
ONE_DATE = {k: v for k, v in TWO_DATES.items() if k != "reports_in"}


@pytest.mark.parametrize(
    ("reports_in", "breaks_in", "wanted"),
    [
        ("2026.10", "2027.10", True),
        (None, "2027.10", False),
        ("", "2027.10", False),
        ("2027.10", "2027.10", False),
        ("2027.11", "2027.10", False),
        # 2027.9 is text-greater than 2027.10 and release-lesser.
        ("2027.9", "2027.10", True),
    ],
)
def test_a_second_date_only_exists_when_it_is_ahead_of_the_removal(
    reports_in, breaks_in, wanted
):
    assert reports_before_removal(reports_in, breaks_in) is wanted


def test_the_report_release_is_phrased_as_something_starting():
    """It sits next to a later removal, so it says what begins, not what ends."""
    said = describe_report("2026.10", 20)
    assert said.startswith("Logs a warning from Home Assistant 2026.10")


def test_the_board_carries_both_releases_on_a_rule_that_has_both():
    item = rule_item("device-entry-config-entries", TWO_DATES, TODAY)
    assert 'class="reports"' in item
    assert "Logs a warning from Home Assistant 2026.10" in item


def test_the_board_renders_a_single_date_rule_exactly_as_before():
    """No second date, no extra element, no layout change."""
    item = rule_item("device-entry-config-entries", ONE_DATE, TODAY)
    assert "reports" not in item
    assert item.endswith("source</a></li>")


def test_a_report_release_that_is_not_ahead_never_reaches_a_renderer(tmp_path):
    """Dropped where the rules are loaded, so no renderer has to decide when
    two dates are really one."""
    path = tmp_path / "manual_rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "x",
                        "kind": "call",
                        "symbol": "f",
                        "message": "m",
                        "breaks_in": "2027.10",
                        "reports_in": "2027.10",
                        "source": "s",
                        "match": {
                            "type": "call",
                            "names": ["f"],
                            "modules": ["m"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (loaded,) = load_manual_rules(path)
    assert "reports_in" not in loaded


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("fits", "fits"),
        ("one two three four", "one two..."),
        ("ends on a comma, then more", "ends on a..."),
        # Nothing to break on, so the hard cut stands.
        ("supercalifragilistic", "supercalif..."),
    ],
)
def test_a_message_too_long_to_show_is_cut_at_a_word(text, wanted):
    assert clip(text, 10) == wanted


def test_the_board_does_not_cut_a_quote_mid_word():
    """Every rule message quotes the post it came from, and the re-dated ones
    quote two sentences, so the cut lands inside the quotation."""
    long_rule = {**TWO_DATES, "message": "Calls modbus.get_hub. " + "word " * 80}
    item = rule_item("modbus-get-hub", long_rule, TODAY)
    assert "word... " in item


def test_the_feed_carries_the_warning_release_without_a_countdown():
    """A feed item is written once and read months later, so it names the
    release and its month and leaves the days-away count to the board."""
    item = warns({**TWO_DATES, "breaks_in": "2027.10", "reports_in": "2026.10"})
    assert "Logs a warning from Home Assistant 2026.10 (October 2026)." in item
    assert "away" not in item


def test_the_feed_says_nothing_extra_about_a_single_date_rule():
    assert warns(ONE_DATE) == ""


def test_a_feed_label_still_fits_after_the_shared_cut():
    long_prose = {"id": "core-prose-x", "symbol": "sets an invalid entity ID " * 6}
    assert len(rule_label(long_prose)) <= MAX_LABEL
