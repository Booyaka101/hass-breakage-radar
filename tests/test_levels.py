"""Three levels: broken now, imminent, and everything further out.

The whole point of the tiering is that urgency decides the *presentation*: a
deadline you can act on this month gets its own alert, a deadline a year out
gets summarised. All dates are injected, so these never depend on the clock.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from custom_components.breakage_radar.const import ALERT_WINDOW_DAYS, DOMAIN, ISSUE_ID
from custom_components.breakage_radar.report import (
    build_report,
    release_estimated_date,
)


@pytest.fixture
def sample_index(fixtures_dir):
    return json.loads(
        (fixtures_dir / "index_sample.json").read_text(encoding="utf-8")
    )


def _report(index, **kwargs):
    return build_report(index, {"fixture_tracker": "0.1.0"}, **kwargs)


# --------------------------------------------------------------------------- #
# release -> estimated date
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "release,expected",
    [
        ("2027.5", date(2027, 5, 5)),
        ("2027.12", date(2027, 12, 1)),
        ("2026.10.3", date(2026, 10, 7)),
        ("2027", None),
        ("", None),
        ("not.a.version", None),
        ("2027.13", None),
    ],
)
def test_release_estimated_date(release, expected):
    """The first Wednesday of the month -- Home Assistant's published release
    day (home-assistant.io/faq/release/), exact on all eight 2026.x releases."""
    assert release_estimated_date(release) == expected


@pytest.mark.parametrize(
    "release,actual",
    [
        # Every 2026 release to date, from the release-notes blog.
        ("2026.1", date(2026, 1, 7)),
        ("2026.2", date(2026, 2, 4)),
        ("2026.3", date(2026, 3, 4)),
        ("2026.4", date(2026, 4, 1)),
        ("2026.5", date(2026, 5, 6)),
        ("2026.6", date(2026, 6, 3)),
        ("2026.7", date(2026, 7, 1)),
        ("2026.8", date(2026, 8, 5)),
    ],
)
def test_the_estimate_matches_every_real_2026_release(release, actual):
    assert release_estimated_date(release) == actual


# --------------------------------------------------------------------------- #
# the three levels
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "today,running,expected",
    [
        # Deadline 2027.5 lands 2027-05-05, the first Wednesday of May.
        (date(2026, 8, 11), "2026.9", "upcoming"),   # 9 months out
        (date(2027, 3, 20), "2027.3", "upcoming"),   # 46 days out
        (date(2027, 4, 6), "2027.4", "imminent"),    # 29 days out
        (date(2027, 5, 4), "2027.4", "imminent"),    # 1 day out
        (date(2027, 5, 6), "2027.5", "broken_now"),  # running the release
        (date(2028, 1, 1), "2028.1", "broken_now"),  # long past
    ],
)
def test_findings_are_levelled_by_urgency(sample_index, today, running, expected):
    report = _report(sample_index, current_version=running, today=today)
    assert report["details"][0]["when"] == expected


def test_the_alert_window_boundary_is_inclusive(sample_index):
    """Exactly ALERT_WINDOW_DAYS away still alerts; one day more does not."""
    deadline = date(2027, 5, 5)
    on_the_edge = date(
        deadline.year, deadline.month, deadline.day
    ).toordinal() - ALERT_WINDOW_DAYS
    inside = _report(
        sample_index,
        current_version="2027.4",
        today=date.fromordinal(on_the_edge),
    )
    outside = _report(
        sample_index,
        current_version="2027.3",
        today=date.fromordinal(on_the_edge - 1),
    )
    assert inside["details"][0]["when"] == "imminent"
    assert inside["details"][0]["days_until"] == ALERT_WINDOW_DAYS
    assert outside["details"][0]["when"] == "upcoming"


def test_broken_now_is_decided_by_version_not_by_the_date_estimate(sample_index):
    """The date is only ever an estimate; the running version is exact, so it
    wins for 'already broken'."""
    # The estimate says the release is still 24 days away, but this system is
    # already running it -- an early release, or a date estimate off by days.
    report = _report(sample_index, current_version="2027.5", today=date(2027, 4, 11))
    assert report["details"][0]["when"] == "broken_now"
    assert report["broken_now"] == {"fixture_tracker": "2027.5"}
    assert report["imminent"] == {}


def test_imminent_carries_the_release_and_days(sample_index):
    report = _report(sample_index, current_version="2027.4", today=date(2027, 4, 11))
    assert report["imminent"] == {
        "fixture_tracker": {"release": "2027.5", "days": 24}
    }
    assert report["imminent_count"] == 1
    assert report["details"][0]["days_until"] == 24


def test_without_a_date_nothing_is_imminent(sample_index):
    """No clock, no opinion -- degrade to the summary rather than guessing."""
    report = _report(sample_index, current_version="2027.4")
    assert report["details"][0]["when"] == "upcoming"
    assert report["details"][0]["days_until"] is None
    assert report["imminent"] == {}
    assert report["summarised_domains"] == ["fixture_tracker"]


def test_a_custom_window_changes_what_counts_as_imminent(sample_index):
    far = _report(
        sample_index,
        current_version="2027.1",
        today=date(2027, 1, 15),
        alert_window_days=7,
    )
    wide = _report(
        sample_index,
        current_version="2027.1",
        today=date(2027, 1, 15),
        alert_window_days=180,
    )
    assert far["details"][0]["when"] == "upcoming"
    assert wide["details"][0]["when"] == "imminent"


# --------------------------------------------------------------------------- #
# grouping: only the non-urgent remainder is summarised
# --------------------------------------------------------------------------- #


def test_urgent_domains_leave_the_summary(sample_index):
    urgent = _report(sample_index, current_version="2027.4", today=date(2027, 4, 11))
    assert urgent["imminent_count"] == 1
    assert urgent["summarised_domains"] == []

    later = _report(sample_index, current_version="2026.9", today=date(2026, 8, 11))
    assert later["imminent_count"] == 0
    assert later["summarised_domains"] == ["fixture_tracker"]
    # Still affected either way -- levelling changes presentation, not verdict.
    assert urgent["affected_count"] == later["affected_count"] == 1


# --------------------------------------------------------------------------- #
# repairs: an alert each for urgent, one summary for the rest
# --------------------------------------------------------------------------- #


def _sync(report):
    from custom_components.breakage_radar.repairs import async_sync_issue

    async_sync_issue(None, report)


def test_imminent_raises_its_own_warning_issue_and_steps_down_again(sample_index):
    from homeassistant.helpers import issue_registry as ir

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own harness")

    ir.created.clear()
    _sync(_report(sample_index, current_version="2027.4", today=date(2027, 4, 11)))

    key = (DOMAIN, "imminent_fixture_tracker")
    assert key in ir.created
    assert ir.created[key]["severity"] == ir.IssueSeverity.WARNING
    assert ir.created[key]["translation_placeholders"] == {
        "domain": "fixture_tracker",
        "release": "2027.5",
        "days": "24",
    }
    # Nothing left over, so no summary issue.
    assert (DOMAIN, ISSUE_ID) not in ir.created

    # Time travel backwards (a fresh install on an older system): the deadline
    # is far away again, so the alert must step back down to the summary.
    _sync(_report(sample_index, current_version="2026.9", today=date(2026, 8, 11)))
    assert key not in ir.created
    assert (DOMAIN, ISSUE_ID) in ir.created


def test_an_imminent_alert_is_replaced_by_the_broken_alert(sample_index):
    from homeassistant.helpers import issue_registry as ir

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own harness")

    ir.created.clear()
    _sync(_report(sample_index, current_version="2027.4", today=date(2027, 4, 11)))
    assert (DOMAIN, "imminent_fixture_tracker") in ir.created

    # The user upgrades onto the breaking release.
    _sync(_report(sample_index, current_version="2027.5", today=date(2027, 5, 6)))
    assert (DOMAIN, "imminent_fixture_tracker") not in ir.created
    assert (DOMAIN, "broken_now_fixture_tracker") in ir.created
    assert (
        ir.created[(DOMAIN, "broken_now_fixture_tracker")]["severity"]
        == ir.IssueSeverity.ERROR
    )


def test_the_summary_only_counts_what_it_still_lists(sample_index):
    from homeassistant.helpers import issue_registry as ir

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own harness")

    ir.created.clear()
    report = _report(sample_index, current_version="2026.9", today=date(2026, 8, 11))
    _sync(report)
    placeholders = ir.created[(DOMAIN, ISSUE_ID)]["translation_placeholders"]
    assert placeholders["count"] == "1"
    assert placeholders["integrations"] == "fixture_tracker"
    assert ir.created[(DOMAIN, ISSUE_ID)]["severity"] == ir.IssueSeverity.WARNING
