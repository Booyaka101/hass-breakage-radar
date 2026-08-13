"""The RSS feed of announced removals.

Asked for on the announcement thread: following the index means diffing a
snapshot yourself, so the feed answers "what is new" instead.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tools.feed import MAX_ITEMS, build, describe, update_first_seen


@pytest.fixture
def payload():
    return {
        "generated_utc": "2026-08-12T04:00:00Z",
        "rules": [
            {
                "id": "legacy-device-tracker-platform",
                "breaks_in": "2027.5",
                "symbol": "setup_scanner",
                "message": "Implements the legacy device tracker platform API.",
                "source": "https://developers.home-assistant.io/blog/legacy/",
                "confidence": "high",
                "replacement": "ScannerEntity",
                "repos_hit": 11,
            },
            {
                "id": "core-call-verify-domain-control",
                "breaks_in": "2026.10",
                "symbol": "verify_domain_control",
                "message": "Passes hass where it is ignored.",
                "source": "homeassistant/helpers/service.py:418",
                "source_url": "https://github.com/home-assistant/core/blob/dev/x.py#L1",
                "confidence": "medium",
                "repos_hit": 1,
            },
        ],
    }


def test_the_feed_is_valid_rss(payload):
    root = ET.fromstring(build(payload, {}))
    channel = root.find("channel")
    assert root.get("version") == "2.0"
    assert channel.findtext("title").startswith("Breakage Radar")
    assert len(channel.findall("item")) == 2


def test_each_removal_is_one_item_with_a_stable_id(payload):
    channel = ET.fromstring(build(payload, {})).find("channel")
    guids = [i.findtext("guid") for i in channel.findall("item")]
    assert guids == [
        "breakage-radar:core-call-verify-domain-control",
        "breakage-radar:legacy-device-tracker-platform",
    ]
    # Same input, same ids, so a reader does not re-notify.
    again = ET.fromstring(build(payload, {})).find("channel")
    assert [i.findtext("guid") for i in again.findall("item")] == guids


def test_the_soonest_deadline_comes_first_when_dates_tie(payload):
    """Every rule shares a date on the first run, so ordering has to mean
    something else."""
    channel = ET.fromstring(build(payload, {})).find("channel")
    assert [i.findtext("category") for i in channel.findall("item")] == [
        "2026.10",
        "2027.5",
    ]


def test_a_rule_keeps_the_date_it_was_first_published(payload):
    seen = update_first_seen(payload["rules"], {}, now="2026-08-01T00:00:00Z")
    assert set(seen) == {"legacy-device-tracker-platform", "core-call-verify-domain-control"}

    payload["rules"].append({
        "id": "brand-new", "breaks_in": "2028.1", "symbol": "x",
        "message": "m", "source": "s", "confidence": "low", "repos_hit": 0,
    })
    seen = update_first_seen(payload["rules"], seen, now="2026-09-01T00:00:00Z")
    assert seen["legacy-device-tracker-platform"] == "2026-08-01T00:00:00Z"
    assert seen["brand-new"] == "2026-09-01T00:00:00Z"

    channel = ET.fromstring(build(payload, seen)).find("channel")
    assert channel.findall("item")[0].findtext("category") == "2028.1"


def test_an_item_links_somewhere_a_browser_can_open(payload):
    """A rule's source can be a bare 'file.py:418', which is not a URL."""
    channel = ET.fromstring(build(payload, {})).find("channel")
    for item in channel.findall("item"):
        assert item.findtext("link").startswith("http")


def test_the_description_says_how_many_integrations_are_affected(payload):
    text = describe(payload["rules"][0], 11)
    assert "11 integration(s)" in text
    assert "ScannerEntity" in text


def test_the_feed_is_capped(payload):
    payload["rules"] = [
        {**payload["rules"][0], "id": f"rule-{n:03d}", "breaks_in": f"2027.{n % 12 + 1}"}
        for n in range(MAX_ITEMS + 20)
    ]
    channel = ET.fromstring(build(payload, {})).find("channel")
    assert len(channel.findall("item")) == MAX_ITEMS
