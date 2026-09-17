"""The removal releases the shipped rules actually claim.

Home Assistant moved the `DeviceEntry` config-entry properties from 2027.8 to
2027.10 on 2026-09-15, "two releases later than the 2027.8 given in the earlier
post". The same post says the WebSocket fields are on their own schedule and
are not affected, so the card rules keep 2027.8. Getting either wrong tells a
maintainer they have two releases they do not have, or costs them two they do.
"""

from __future__ import annotations

import pytest

from tools.rules_engine import is_pending, load_rules, matchable_rules

MOVED_TO_2027_10 = [
    "device-entry-config-entries",
    "device-entry-primary-config-entry",
    "modbus-get-hub",
]

#: Named in the same correction but not covered by it: these are the WebSocket
#: fields a Lovelace card reads, which the 2026-09-15 post leaves at 2027.8.
STILL_2027_8 = [
    "device-registry-config-entries-field",
    "device-registry-config-entries-subentries-field",
    "device-registry-primary-config-entry-field",
    "device-info-via-device",
    "device-registry-config-entry-mutation-params",
    "device-registry-async-get-device",
    "async-initialize-triggers-home-assistant-start",
]


@pytest.fixture(scope="module")
def by_id(shipped_rules) -> dict:
    return {rule["id"]: rule for rule in shipped_rules["rules"]}


@pytest.mark.parametrize("rule_id", MOVED_TO_2027_10)
def test_the_python_properties_are_removed_in_2027_10(rule_id, by_id):
    assert by_id[rule_id]["breaks_in"] == "2027.10"


@pytest.mark.parametrize("rule_id", MOVED_TO_2027_10)
def test_each_moved_rule_says_when_it_starts_warning(rule_id, by_id):
    assert by_id[rule_id]["reports_in"] == "2026.10"


@pytest.mark.parametrize("rule_id", STILL_2027_8)
def test_the_rules_the_correction_does_not_cover_keep_2027_8(rule_id, by_id):
    assert by_id[rule_id]["breaks_in"] == "2027.8"
    assert "reports_in" not in by_id[rule_id]


def test_the_moved_rules_cite_the_post_that_moved_them(by_id):
    for rule_id in ("device-entry-config-entries", "device-entry-primary-config-entry"):
        assert by_id[rule_id]["source"].endswith(
            "/blog/2026/09/15/device-entry-config-entries-deprecation"
        )


def test_nothing_retires_at_2027_8_that_is_removed_in_2027_10(shipped_rules):
    """Retirement keys off the removal, not off the warning. A crawl running
    when 2027.8 is the oldest release anyone runs must still match these."""
    moved = [r for r in shipped_rules["rules"] if r["id"] in MOVED_TO_2027_10]
    assert len(moved) == len(MOVED_TO_2027_10)
    for rule in moved:
        assert is_pending(rule["breaks_in"], "2027.9") is True
    still = matchable_rules(load_rules(moved), current_version="2027.9")
    assert {r.id for r in still} == set(MOVED_TO_2027_10)


def test_the_warning_release_does_not_retire_a_rule_early(shipped_rules):
    """2026.10 is already shipped by the time 2027.x runs; a rule keyed off it
    would vanish from the board while the API is still there to remove."""
    moved = [r for r in shipped_rules["rules"] if r["id"] in MOVED_TO_2027_10]
    for rule in moved:
        assert is_pending(rule["reports_in"], "2027.9") is False
