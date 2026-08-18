"""The per-user ignore list (issue #19).

HACS is the case that prompted it: everyone who installs Breakage Radar
installs it through HACS, so a real finding against HACS itself reaches every
user and none of them can act on it. The answer is to let the user drop it
rather than to exclude anything on their behalf, so the default here is empty
and the tests below mostly guard "ignoring one thing changes nothing else".
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.breakage_radar.config_flow import BreakageRadarOptionsFlow
from custom_components.breakage_radar.const import (
    CONF_ALERT_WINDOW_DAYS,
    CONF_IGNORED_DOMAINS,
    DOMAIN,
)
from custom_components.breakage_radar.report import build_report

INSTALLED = {"fixture_tracker": "0.1.0", "lookalike_tracker": "0.1.0"}


def _report(index, installed=None, **kwargs):
    return build_report(index, dict(installed or INSTALLED), **kwargs)


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def test_nothing_is_ignored_by_default(sample_index):
    report = _report(sample_index)

    assert report["ignored_domains"] == []
    assert report["affected_domains"] == ["fixture_tracker"]


def test_an_ignored_domain_leaves_no_trace_in_the_report(sample_index):
    report = _report(sample_index, ignored_domains=["fixture_tracker"])

    assert report["ignored_domains"] == ["fixture_tracker"]
    assert report["affected_count"] == 0
    assert report["affected_domains"] == []
    assert report["details"] == []
    assert report["total_findings"] == 0
    assert report["schedule"] == []
    assert report["earliest_release"] is None


def test_ignoring_one_integration_leaves_the_others_alone(sample_index):
    report = _report(sample_index, ignored_domains=["lookalike_tracker"])

    assert report["ignored_domains"] == ["lookalike_tracker"]
    assert report["affected_domains"] == ["fixture_tracker"]
    assert "lookalike_tracker" not in report["clean_domains"]


def test_an_ignored_domain_is_out_of_the_counts_entirely(sample_index):
    """Not just hidden from the list: the "n of m" has to agree with it."""
    both = _report(sample_index)
    one = _report(sample_index, ignored_domains=["fixture_tracker"])

    assert both["installed_count"] == 2
    assert one["installed_count"] == 1


def test_ignoring_something_that_is_not_installed_does_nothing(sample_index):
    report = _report(sample_index, ignored_domains=["never_installed"])

    assert report["ignored_domains"] == []
    assert report["affected_domains"] == ["fixture_tracker"]


def test_an_ignored_domain_raises_no_alert_however_urgent(sample_index):
    """The levelling runs after the filter, so even "broken now" stays quiet."""
    loud = _report(sample_index, current_version="2027.5", today=date(2027, 5, 5))
    assert loud["broken_now"] == {"fixture_tracker": "2027.5"}

    quiet = _report(
        sample_index,
        current_version="2027.5",
        today=date(2027, 5, 5),
        ignored_domains=["fixture_tracker"],
    )
    assert quiet["broken_now"] == {}
    assert quiet["imminent"] == {}
    assert quiet["summarised_domains"] == []


def test_an_ignored_domain_gets_no_repairs_card(sample_index):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.breakage_radar.repairs import async_sync_issue

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own harness")

    ir.created.clear()
    async_sync_issue(
        None,
        _report(
            sample_index,
            current_version="2027.5",
            today=date(2027, 5, 5),
            ignored_domains=["fixture_tracker"],
        ),
    )

    assert not [key for key in ir.created if key[0] == DOMAIN]


def test_the_sensor_says_what_is_being_left_out(sample_index):
    """Otherwise a missing integration looks like a bug rather than a setting."""
    from conftest import FakeCoordinator

    from custom_components.breakage_radar.sensor import BreakageRadarSensor

    report = _report(sample_index, ignored_domains=["fixture_tracker"])
    sensor = BreakageRadarSensor(FakeCoordinator(report))

    assert sensor.extra_state_attributes["ignored_domains"] == ["fixture_tracker"]
    assert sensor.native_value == 0


# --------------------------------------------------------------------------- #
# the options flow
# --------------------------------------------------------------------------- #


def _flow(*, options=None, affected=None):
    """A flow with its own entry and hass, so tests cannot leak into each other
    through the shared stubs in conftest."""
    flow = BreakageRadarOptionsFlow()
    entry = SimpleNamespace(entry_id="test-entry", options=options or {})
    coordinator = SimpleNamespace(data={"affected_domains": affected or []})
    flow.config_entry = entry
    flow.hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})
    return flow


def _field(form, key):
    schema = form["data_schema"].schema
    marker = next(m for m in schema if m.schema == key)
    return marker, schema[marker]


def test_the_picker_offers_what_is_actually_affected():
    form = asyncio.run(_flow(affected=["hacs", "pycupra"]).async_step_init())

    _, selector = _field(form, CONF_IGNORED_DOMAINS)
    assert selector.config.options == ["hacs", "pycupra"]
    assert selector.config.multiple is True


def test_an_already_ignored_domain_stays_in_the_picker():
    """It is filtered out of the report, so the union is the only thing
    keeping it visible in the list that put it there."""
    form = asyncio.run(
        _flow(
            options={CONF_IGNORED_DOMAINS: ["hacs"]}, affected=["pycupra"]
        ).async_step_init()
    )

    marker, selector = _field(form, CONF_IGNORED_DOMAINS)
    assert selector.config.options == ["hacs", "pycupra"]
    assert marker.default == ["hacs"]


def test_a_stored_domain_survives_dropping_off_the_affected_list():
    """Fixed upstream today, regressed in six months. Without custom_value the
    selector discards the stored value and the ignore is silently undone."""
    form = asyncio.run(
        _flow(options={CONF_IGNORED_DOMAINS: ["hacs"]}, affected=[]).async_step_init()
    )

    _, selector = _field(form, CONF_IGNORED_DOMAINS)
    assert selector.config.custom_value is True


def test_saving_keeps_both_settings():
    flow = _flow(affected=["hacs"])
    result = asyncio.run(
        flow.async_step_init(
            {CONF_ALERT_WINDOW_DAYS: "90", CONF_IGNORED_DOMAINS: ["hacs"]}
        )
    )

    assert result["data"] == {
        CONF_ALERT_WINDOW_DAYS: 90,
        CONF_IGNORED_DOMAINS: ["hacs"],
    }


def test_saving_with_nothing_ignored_stores_an_empty_list():
    flow = _flow()
    result = asyncio.run(flow.async_step_init({CONF_ALERT_WINDOW_DAYS: "30"}))

    assert result["data"][CONF_IGNORED_DOMAINS] == []
