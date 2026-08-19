"""The RSS feed of announced removals.

Asked for on the announcement thread: following the index means diffing a
snapshot yourself, so the feed answers "what is new" instead. One item per
release, carrying that release's own content, because an item a reader can
display beats a bookmark (#28).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tools.feed import (
    MAX_ITEMS,
    MAX_LABEL,
    MAX_TABLE_ROWS,
    STYLESHEET,
    build,
    describe,
    group_by_release,
    guid_for,
    published_for,
    rule_label,
    update_first_seen,
)

RULES = [
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
]


def _integration(name, domain, release, rule_id, stars=0):
    return {
        "full_name": name,
        "domain": domain,
        "version": "1.0.0",
        "repo_url": f"https://github.com/{name}",
        "stargazers_count": stars,
        "findings": [
            {"rule_id": rule_id, "breaks_in": release, "file": "x.py", "line": 1}
        ],
    }


@pytest.fixture
def payload():
    return {
        "generated_utc": "2026-08-12T04:00:00Z",
        # Copied, or a test that appends a rule leaks into the next one.
        "rules": [dict(rule) for rule in RULES],
        "integrations": [
            _integration("a/one", "one", "2027.5", "legacy-device-tracker-platform", 50),
            _integration("b/two", "two", "2027.5", "legacy-device-tracker-platform", 10),
            _integration("c/three", "three", "2026.10", "core-call-verify-domain-control"),
        ],
    }


def _channel(feed):
    return ET.fromstring(feed).find("channel")


def test_the_feed_is_valid_rss(payload):
    root = ET.fromstring(build(payload, {}))
    channel = root.find("channel")
    assert root.get("version") == "2.0"
    assert channel.findtext("title").startswith("Breakage Radar")


def test_one_item_per_release_not_per_rule(payload):
    channel = _channel(build(payload, {}))
    titles = [i.findtext("title") for i in channel.findall("item")]
    assert titles == ["Home Assistant 2026.10", "Home Assistant 2027.5"]


def test_an_item_links_to_its_own_section_of_the_board(payload):
    channel = _channel(build(payload, {}))
    links = [i.findtext("link") for i in channel.findall("item")]
    assert links[0].endswith("#release-2026.10")
    assert all(link.startswith("https://") for link in links)


def test_the_body_carries_the_release_contents(payload):
    channel = _channel(build(payload, {}))
    body = channel.findall("item")[1].findtext("description")

    # The rules, by symbol rather than by slug.
    assert "setup_scanner" in body
    assert "Implements the legacy device tracker platform API." in body
    # The integrations, linked.
    assert "https://github.com/a/one" in body
    assert "<table>" in body
    # And a way back to the whole list.
    assert "#release-2027.5" in body


def test_the_body_is_markup_not_escaped_text(payload):
    """A reader showing &lt;table&gt; as text is the failure this guards."""
    feed = build(payload, {})
    assert "<![CDATA[" in feed
    assert "&lt;table&gt;" not in feed


def test_a_title_carries_no_count(payload):
    """The count moves daily; a title that moves makes an unchanged item look
    new in some readers."""
    for item in _channel(build(payload, {})).findall("item"):
        assert item.findtext("title").count(" ") == 2


# --------------------------------------------------------------------------- #
# what counts as news
# --------------------------------------------------------------------------- #


def test_a_release_is_dated_by_its_newest_rule():
    seen = {"old": "2026-01-01T00:00:00Z", "new": "2026-08-01T00:00:00Z"}
    rules = [{"id": "old"}, {"id": "new"}]
    assert published_for(rules, seen, "2026-09-01T00:00:00Z") == "2026-08-01T00:00:00Z"


def test_more_integrations_do_not_republish_a_release(payload):
    """The crawl widens daily. If that moved the date, every subscriber would
    be re-notified five times a day."""
    seen = update_first_seen(payload["rules"], {}, now="2026-08-01T00:00:00Z")
    before = _channel(build(payload, seen)).findall("item")

    payload["integrations"].append(
        _integration("d/four", "four", "2027.5", "legacy-device-tracker-platform", 99)
    )
    after = _channel(build(payload, seen)).findall("item")

    assert [i.findtext("pubDate") for i in before] == [
        i.findtext("pubDate") for i in after
    ]
    assert [i.findtext("guid") for i in before] == [i.findtext("guid") for i in after]


def test_a_new_rule_resurfaces_its_release(payload):
    seen = update_first_seen(payload["rules"], {}, now="2026-08-01T00:00:00Z")
    was = _channel(build(payload, seen)).findall("item")[1]

    payload["rules"].append(
        {
            "id": "brand-new",
            "breaks_in": "2027.5",
            "symbol": "something_else",
            "message": "m",
            "source": "s",
            "confidence": "low",
            "repos_hit": 0,
        }
    )
    payload["integrations"].append(
        _integration("e/five", "five", "2027.5", "brand-new")
    )
    seen = update_first_seen(payload["rules"], seen, now="2026-09-01T00:00:00Z")
    now = _channel(build(payload, seen)).findall("item")[0]

    assert now.findtext("title") == was.findtext("title")
    assert now.findtext("pubDate") != was.findtext("pubDate")
    # The guid folds in the rule set, so a reader treats it as new.
    assert now.findtext("guid") != was.findtext("guid")


def test_the_same_input_gives_the_same_guid(payload):
    first = [i.findtext("guid") for i in _channel(build(payload, {})).findall("item")]
    again = [i.findtext("guid") for i in _channel(build(payload, {})).findall("item")]
    assert first == again
    assert all(g.startswith("breakage-radar:release:") for g in first)


def test_a_rule_keeps_the_date_it_was_first_published(payload):
    seen = update_first_seen(payload["rules"], {}, now="2026-08-01T00:00:00Z")
    payload["rules"].append(
        {"id": "brand-new", "breaks_in": "2028.1", "symbol": "x", "message": "m"}
    )
    seen = update_first_seen(payload["rules"], seen, now="2026-09-01T00:00:00Z")

    assert seen["legacy-device-tracker-platform"] == "2026-08-01T00:00:00Z"
    assert seen["brand-new"] == "2026-09-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# size, which is why the table is capped
# --------------------------------------------------------------------------- #


def test_the_table_is_capped_and_says_so(payload):
    payload["integrations"] += [
        _integration(f"x/repo{n}", f"d{n}", "2027.5", "legacy-device-tracker-platform", n)
        for n in range(MAX_TABLE_ROWS + 10)
    ]
    body = _channel(build(payload, {})).findall("item")[1].findtext("description")

    # "<tr><td>" is a data row; the header row is "<tr><th>".
    assert body.count("<tr><td>") == MAX_TABLE_ROWS
    assert "more on the board" in body


def test_the_busiest_integrations_are_the_ones_listed(payload):
    payload["integrations"] += [
        _integration(f"x/repo{n}", f"d{n}", "2027.5", "legacy-device-tracker-platform", n)
        for n in range(MAX_TABLE_ROWS + 10)
    ]
    body = _channel(build(payload, {})).findall("item")[1].findtext("description")

    assert "x/repo29" in body      # most stars
    assert "x/repo0" not in body   # fewest


def test_the_feed_is_capped_by_release(payload):
    payload["integrations"] = [
        _integration(f"x/repo{n}", f"d{n}", f"20{27 + n}.5", "legacy-device-tracker-platform")
        for n in range(MAX_ITEMS + 5)
    ]
    assert len(_channel(build(payload, {})).findall("item")) == MAX_ITEMS


# --------------------------------------------------------------------------- #
# rendering in a browser (#27)
# --------------------------------------------------------------------------- #


def test_the_feed_points_at_a_stylesheet_that_exists(payload, repo_root):
    feed = build(payload, {})
    assert f'<?xml-stylesheet type="text/xsl" href="{STYLESHEET}"?>' in feed
    # A missing stylesheet renders as a blank page, which is worse than XML.
    assert (repo_root / "docs" / STYLESHEET).is_file()


def test_the_stylesheet_path_is_relative():
    """Browsers refuse a cross-origin XSL, so this cannot become absolute."""
    assert "://" not in STYLESHEET


def test_the_stylesheet_is_well_formed(repo_root):
    ET.parse(repo_root / "docs" / STYLESHEET)


# --------------------------------------------------------------------------- #
# escaping and labels
# --------------------------------------------------------------------------- #


def test_a_prose_rule_gets_a_readable_label_not_a_slug():
    rule = {
        "id": "core-prose-sets-an-invalid-entity-id-in-most-cases-entities",
        "breaks_in": "2027.2",
        "symbol": "sets an invalid entity ID: '{...}'. In most cases, entities "
        "should not set entity IDs themselves.",
    }
    label = rule_label(rule)
    assert label.startswith("sets an invalid entity ID")
    assert "core-prose" not in label
    assert len(label) <= MAX_LABEL
    assert not label.rstrip("…").endswith(" ")


def test_a_label_short_enough_is_left_alone():
    assert rule_label({"id": "x", "symbol": "setup_scanner"}) == "setup_scanner"


def test_markup_in_a_message_cannot_break_the_feed(payload):
    payload["rules"][0]["message"] = "uses <Thing> & </item> in a message"
    feed = build(payload, {})
    ET.fromstring(feed)  # would raise if the escaping were wrong
    assert "&lt;Thing&gt;" in feed


def test_a_cdata_terminator_in_content_cannot_break_the_feed(payload):
    """Nothing escapes inside CDATA, so ']]>' has to be split by hand."""
    payload["rules"][0]["message"] = "a message containing ]]> in the middle"
    ET.fromstring(build(payload, {}))


def test_grouping_counts_each_integration_once(payload):
    payload["integrations"][0]["findings"].append(
        {"rule_id": "legacy-device-tracker-platform", "breaks_in": "2027.5",
         "file": "y.py", "line": 2}
    )
    rules, integrations = group_by_release(payload)["2027.5"]
    assert len(integrations) == 2
    assert len(rules) == 1


def test_describe_reads_as_a_sentence_for_a_single_integration(payload):
    payload["integrations"] = [payload["integrations"][2]]
    rules, integrations = group_by_release(payload)["2026.10"]
    body = describe("2026.10", rules, integrations)
    assert "removes 1 API." in body
    assert "1 HACS custom integration in the catalogue still uses it." in body


def test_guid_changes_only_with_the_rule_set():
    assert guid_for("2027.5", [{"id": "a"}]) == guid_for("2027.5", [{"id": "a"}])
    assert guid_for("2027.5", [{"id": "a"}]) != guid_for("2027.5", [{"id": "b"}])
    assert guid_for("2027.5", [{"id": "a"}]) != guid_for("2027.7", [{"id": "a"}])


def test_backticks_in_a_message_become_code(payload):
    """Rule messages are quoted prose that marks symbols with backticks, and a
    reader shows those literally unless they are turned into markup."""
    payload["rules"][0]["message"] = "passes `hass` to `async_extract_entities`"
    body = _channel(build(payload, {})).findall("item")[1].findtext("description")
    assert "<code>hass</code>" in body
    assert "`hass`" not in body


def test_a_message_that_does_not_end_in_a_stop_gets_one(payload):
    """Otherwise it runs straight into the sentence that follows it."""
    payload["rules"][0]["message"] = "doesn't specify unit_class when calling it"
    body = _channel(build(payload, {})).findall("item")[1].findtext("description")
    assert "calling it. Used by 11 integrations." in body


def test_an_uncapped_item_gets_one_board_link_not_two(payload):
    """A full table used to be followed by both "and N more" and a second
    "full list" link, pointing at the same anchor."""
    body = _channel(build(payload, {})).findall("item")[1].findtext("description")
    assert body.count("#release-2027.5") == 1
    assert "more on the board" not in body
