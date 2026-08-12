"""The dated schedule and the configurable alert window (issue #3).

The complaint was "I'd like to see when an integration will break. Not just
that 13 will break within the next year or so." So the report has to pair each
release with what breaks in it, in words a person can act on.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from custom_components.breakage_radar.const import (
    DOMAIN,
    ISSUE_ID,
    MAX_ALERT_CARDS,
)
from custom_components.breakage_radar.repairs import (
    MAX_SCHEDULE_LINES,
    format_schedule,
)
from custom_components.breakage_radar.report import build_report, describe_when


@pytest.fixture
def sample_index(fixtures_dir):
    return json.loads(
        (fixtures_dir / "index_sample.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def many_releases(sample_index):
    """One index, several integrations, spread across five releases."""
    template = sample_index["integrations"][0]
    finding = template["findings"][0]
    releases = {
        "2026.11": ["alpha", "beta"],
        "2027.2": ["gamma"],
        "2027.5": ["delta", "epsilon", "zeta"],
        "2027.8": ["eta"],
        "2028.1": ["theta"],
    }
    integrations = []
    for release, domains in releases.items():
        for domain in domains:
            integrations.append(
                {
                    **template,
                    "domain": domain,
                    "domains": [domain],
                    "full_name": f"example/{domain}",
                    "findings": [{**finding, "breaks_in": release}],
                }
            )
    sample_index["integrations"] = integrations
    return sample_index, {d: "1.0" for ds in releases.values() for d in ds}


@pytest.mark.parametrize(
    "release,days,expected",
    [
        ("2027.5", None, "May 2027"),
        ("2027.5", -3, "May 2027, already released"),
        ("2027.5", 0, "May 2027, today"),
        ("2027.5", 1, "May 2027, tomorrow"),
        ("2027.5", 21, "May 2027, about 21 days away"),
        ("2027.5", 44, "May 2027, about 44 days away"),
        ("2027.5", 60, "May 2027, about 2 months away"),
        ("2027.5", 267, "May 2027, about 9 months away"),
        ("2027.5", 400, "May 2027, about a year away"),
        ("2027.5", 700, "May 2027, about 1.9 years away"),
        ("nonsense", 30, "nonsense, about 30 days away"),
    ],
)
def test_describe_when_reads_like_a_person_wrote_it(release, days, expected):
    assert describe_when(release, days) == expected


def test_schedule_pairs_every_release_with_what_breaks_in_it(many_releases):
    index, installed = many_releases
    report = build_report(
        index, installed, current_version="2026.9", today=date(2026, 8, 12)
    )

    schedule = report["schedule"]
    assert [group["release"] for group in schedule] == [
        "2026.11",
        "2027.2",
        "2027.5",
        "2027.8",
        "2028.1",
    ]
    assert schedule[0]["domains"] == ["alpha", "beta"]
    assert schedule[0]["count"] == 2
    assert schedule[2]["domains"] == ["delta", "epsilon", "zeta"]
    assert schedule[0]["due"] == "November 2026, about 3 months away"
    # 84 days out, so outside the default 30 day window.
    assert schedule[0]["when"] == "upcoming"


def test_schedule_sorts_by_release_not_alphabetically(sample_index):
    """2027.10 comes after 2027.9, which a string sort gets wrong."""
    template = sample_index["integrations"][0]
    finding = template["findings"][0]
    sample_index["integrations"] = [
        {
            **template,
            "domain": domain,
            "domains": [domain],
            "findings": [{**finding, "breaks_in": release}],
        }
        for domain, release in (("late", "2027.10"), ("early", "2027.9"))
    ]
    report = build_report(
        sample_index,
        {"late": "1.0", "early": "1.0"},
        current_version="2026.9",
        today=date(2026, 8, 12),
    )
    assert [g["release"] for g in report["schedule"]] == ["2027.9", "2027.10"]


def test_a_release_that_is_already_out_is_marked_broken(many_releases):
    index, installed = many_releases
    report = build_report(
        index, installed, current_version="2027.3", today=date(2027, 3, 3)
    )
    by_release = {g["release"]: g for g in report["schedule"]}
    assert by_release["2026.11"]["when"] == "broken_now"
    assert by_release["2027.2"]["when"] == "broken_now"
    assert by_release["2027.5"]["when"] == "upcoming"   # 63 days, outside 30
    assert by_release["2028.1"]["when"] == "upcoming"


# --------------------------------------------------------------------------- #
# how it renders in the repairs card
# --------------------------------------------------------------------------- #


def test_format_schedule_is_one_readable_line_per_release(many_releases):
    index, installed = many_releases
    report = build_report(
        index, installed, current_version="2026.9", today=date(2026, 8, 12)
    )
    text = format_schedule(report["schedule"])

    assert text.splitlines()[0] == (
        "- **November 2026, about 3 months away** (2026.11): `alpha`, `beta`"
    )
    assert (
        "- **May 2027, about 9 months away** (2027.5): "
        "`delta`, `epsilon`, `zeta`" in text
    )
    assert len(text.splitlines()) == 5


def test_format_schedule_can_show_only_the_summarised_domains(many_releases):
    index, installed = many_releases
    report = build_report(
        index, installed, current_version="2026.9", today=date(2026, 8, 12)
    )
    text = format_schedule(report["schedule"], only={"gamma", "eta"})
    assert text.splitlines() == [
        "- **February 2027, about 6 months away** (2027.2): `gamma`",
        "- **August 2027, about a year away** (2027.8): `eta`",
    ]


def test_format_schedule_caps_long_lists(sample_index):
    template = sample_index["integrations"][0]
    finding = template["findings"][0]
    sample_index["integrations"] = [
        {
            **template,
            "domain": f"domain_{n:02d}",
            "domains": [f"domain_{n:02d}"],
            "findings": [{**finding, "breaks_in": f"2027.{n}"}],
        }
        for n in range(1, 13)
    ]
    installed = {f"domain_{n:02d}": "1.0" for n in range(1, 13)}
    report = build_report(
        sample_index, installed, current_version="2026.9", today=date(2026, 8, 12)
    )
    lines = format_schedule(report["schedule"]).splitlines()
    assert len(lines) == MAX_SCHEDULE_LINES + 1
    assert lines[-1].startswith("- ...and 4 more")


def test_the_summary_card_shows_the_schedule(many_releases):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.breakage_radar.repairs import async_sync_issue

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own harness")

    ir.created.clear()
    index, installed = many_releases
    # A 30 day window keeps all five releases in the summary card.
    report = build_report(
        index,
        installed,
        current_version="2026.9",
        today=date(2026, 8, 12),
        alert_window_days=30,
    )
    async_sync_issue(None, report)

    placeholders = ir.created[(DOMAIN, ISSUE_ID)]["translation_placeholders"]
    assert "**November 2026, about 3 months away** (2026.11)" in (
        placeholders["schedule"]
    )
    assert placeholders["noun"] == "integrations"
    assert placeholders["verb"] == "use"
    assert placeholders["earliest_due"] == "November 2026, about 3 months away"
    assert placeholders["window"] == "30"
    assert placeholders["count"] == "8"


# --------------------------------------------------------------------------- #
# the configurable window
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "window,expected_alerts",
    [(30, 0), (90, 1), (180, 2), (365, 4)],
)
def test_the_window_decides_how_much_is_alerted_on(
    many_releases, window, expected_alerts
):
    """A wider window promotes more releases out of the summary."""
    index, installed = many_releases
    report = build_report(
        index,
        installed,
        current_version="2026.9",
        today=date(2026, 8, 12),
        alert_window_days=window,
    )
    alerted = {g["release"] for g in report["schedule"] if g["when"] == "imminent"}
    assert len(alerted) == expected_alerts


def test_only_the_nearest_deadlines_get_their_own_notification(sample_index):
    """A wide window on a busy system must not raise a card per integration.

    Caught by previewing the cards against the live index: a 90 day window
    turned 13 affected integrations into 13 separate notifications.
    """
    template = sample_index["integrations"][0]
    finding = template["findings"][0]
    domains = [f"domain_{n:02d}" for n in range(12)]
    sample_index["integrations"] = [
        {
            **template,
            "domain": domain,
            "domains": [domain],
            "findings": [{**finding, "breaks_in": "2026.11"}],
        }
        for domain in domains
    ]
    report = build_report(
        sample_index,
        {d: "1.0" for d in domains},
        current_version="2026.9",
        today=date(2026, 8, 12),
        alert_window_days=365,
    )

    assert len(report["imminent"]) == MAX_ALERT_CARDS
    # Nothing is lost: the rest are summarised, and the schedule still lists
    # every one of them with its date.
    assert len(report["summarised_domains"]) == len(domains) - MAX_ALERT_CARDS
    assert report["schedule"][0]["count"] == len(domains)


# --------------------------------------------------------------------------- #
# links: the notification has to lead somewhere useful
# --------------------------------------------------------------------------- #


def test_alert_levels_carry_the_links_needed_to_act(sample_index):
    """A notification about someone else's code is only useful if it points
    at that repository."""
    report = build_report(
        sample_index,
        {"fixture_tracker": "0.1.0"},
        current_version="2027.4",
        today=date(2027, 4, 11),
    )
    link = report["links"]["fixture_tracker"]
    assert link["repository"] == "example/fixture-tracker"
    assert link["repo_url"] == "https://github.com/example/fixture-tracker"
    assert link["learn_more"] == sample_index["rules"][0]["source"]


def test_describe_links_points_at_releases_and_issues():
    from custom_components.breakage_radar.repairs import describe_links

    text = describe_links(
        {
            "repository": "example/thing",
            "repo_url": "https://github.com/example/thing",
            "learn_more": "https://developers.home-assistant.io/blog/whatever",
        }
    )
    assert "[example/thing releases](https://github.com/example/thing/releases)" in text
    # Points at existing reports, never at a blank issue form.
    assert "/issues/new" not in text
    assert "https://developers.home-assistant.io/blog/whatever" in text


def test_describe_links_degrades_when_the_repository_is_unknown():
    """A locally scanned fork is not in the index, so there is no repo URL."""
    from custom_components.breakage_radar.repairs import describe_links

    text = describe_links({"repository": "", "repo_url": "", "learn_more": ""})
    assert "integration's own repository" in text
    assert "](" not in text


def test_describe_links_searches_for_an_existing_report(sample_index):
    """Thousands of users filing the same issue would make this tool a
    nuisance to maintainers, so it links the search, not a blank form."""
    from custom_components.breakage_radar.repairs import describe_links

    text = describe_links(
        {
            "repository": "example/thing",
            "repo_url": "https://github.com/example/thing",
            "symbol": "async_extract_config_entry_ids",
            "learn_more": "",
        }
    )
    assert (
        "https://github.com/example/thing/issues?q=is%3Aissue+"
        "async_extract_config_entry_ids" in text
    )
    assert "reaction" in text
    assert "/issues/new" not in text


def test_the_symbol_travels_with_the_link(sample_index):
    report = build_report(
        sample_index,
        {"fixture_tracker": "0.1.0"},
        current_version="2027.4",
        today=date(2027, 4, 11),
    )
    assert report["links"]["fixture_tracker"]["symbol"] == "setup_scanner"


def test_the_sensor_stays_inside_the_recorder_attribute_limit(sample_index):
    """Home Assistant drops state attributes over 16 KB. A system with many
    findings used to produce 19 KB, so the recorder stored nothing."""
    import json as _json

    from custom_components.breakage_radar.sensor import BreakageRadarSensor

    template = sample_index["integrations"][0]
    finding = template["findings"][0]
    domains = [f"integration_number_{n:03d}" for n in range(60)]
    sample_index["integrations"] = [
        {
            **template,
            "domain": domain,
            "domains": [domain],
            "full_name": f"some-long-owner-name/{domain}-for-home-assistant",
            "findings": [
                {
                    **finding,
                    "breaks_in": "2027.5",
                    "file": f"custom_components/{domain}/a/deeply/nested/module.py",
                    "line": n,
                }
                for n in range(1, 6)
            ],
        }
        for domain in domains
    ]
    report = build_report(
        sample_index,
        {d: "1.0.0" for d in domains},
        current_version="2026.9",
        today=date(2026, 8, 12),
    )

    class _Coordinator:
        data = report
        last_update_success = True
        last_error = None
        index_url = "https://booyaka101.github.io/hass-breakage-radar/index.json"

    size = len(_json.dumps(BreakageRadarSensor(_Coordinator()).extra_state_attributes))
    assert report["total_findings"] == 300
    assert size < 16384, f"{size} bytes would be dropped by the recorder"


def test_a_bare_source_reference_is_not_rendered_as_a_link():
    """Core-derived rules cite a file and line, not a URL."""
    from custom_components.breakage_radar.repairs import describe_links

    text = describe_links(
        {"repository": "", "repo_url": "", "learn_more": "homeassistant/helpers/service.py:418"}
    )
    assert "`homeassistant/helpers/service.py:418`" in text
    assert "](homeassistant" not in text


def test_source_url_is_preferred_over_a_bare_source(sample_index):
    sample_index["rules"][0]["source_url"] = "https://github.com/home-assistant/core/blob/dev/x.py#L1"
    sample_index["rules"][0]["source"] = "homeassistant/x.py:1"
    report = build_report(
        sample_index,
        {"fixture_tracker": "0.1.0"},
        current_version="2027.4",
        today=date(2027, 4, 11),
    )
    assert report["links"]["fixture_tracker"]["learn_more"].startswith("https://")
