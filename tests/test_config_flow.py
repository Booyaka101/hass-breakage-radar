"""The config flow and the options flow.

Nothing imported config_flow.py before, so a broken import there would only
have shown up when a user tried to add the integration. That is how issue #1
reached someone's system, so these tests exist mostly to make sure the module
loads and both flows actually run.
"""

from __future__ import annotations

import asyncio
import json

from custom_components.breakage_radar.config_flow import (
    BreakageRadarConfigFlow,
    BreakageRadarOptionsFlow,
)
from custom_components.breakage_radar.const import (
    ALERT_WINDOW_CHOICES,
    ALERT_WINDOW_DAYS,
    CONF_ALERT_WINDOW_DAYS,
    NAME,
)


def test_config_flow_shows_a_confirmation_then_creates_one_entry():
    flow = BreakageRadarConfigFlow()

    form = asyncio.run(flow.async_step_user())
    assert form["type"] == "form"
    assert form["step_id"] == "user"
    assert "index_url" in form["description_placeholders"]

    created = asyncio.run(flow.async_step_user({}))
    assert created["type"] == "create_entry"
    assert created["title"] == NAME


def test_options_flow_offers_the_documented_choices():
    flow = BreakageRadarOptionsFlow()
    form = asyncio.run(flow.async_step_init())

    assert form["type"] == "form"
    marker = next(iter(form["data_schema"].schema))
    assert marker.schema == CONF_ALERT_WINDOW_DAYS
    assert marker.default == str(ALERT_WINDOW_DAYS)

    selector = form["data_schema"].schema[marker]
    assert selector.config.options == [str(d) for d in ALERT_WINDOW_CHOICES]
    assert selector.config.translation_key == CONF_ALERT_WINDOW_DAYS


def test_options_flow_stores_the_window_as_an_int():
    flow = BreakageRadarOptionsFlow()
    result = asyncio.run(flow.async_step_init({CONF_ALERT_WINDOW_DAYS: "180"}))

    assert result["type"] == "create_entry"
    # A selector hands back a string; the coordinator does date maths with it.
    assert result["data"] == {CONF_ALERT_WINDOW_DAYS: 180}
    assert isinstance(result["data"][CONF_ALERT_WINDOW_DAYS], int)


def test_every_offered_choice_has_a_translated_label(repo_root):
    strings = json.loads(
        (repo_root / "custom_components" / "breakage_radar" / "strings.json").read_text(
            encoding="utf-8"
        )
    )
    labels = strings["selector"][CONF_ALERT_WINDOW_DAYS]["options"]
    assert set(labels) == {str(days) for days in ALERT_WINDOW_CHOICES}
    assert all(label.strip() for label in labels.values())


def test_the_default_window_is_one_of_the_choices():
    assert ALERT_WINDOW_DAYS in ALERT_WINDOW_CHOICES


def test_strings_reference_the_real_entity_id(repo_root):
    """The cards tell people where to look, so the id has to be right."""
    from custom_components.breakage_radar.const import DOMAIN

    base = repo_root / "custom_components" / "breakage_radar"
    strings = (base / "strings.json").read_text(encoding="utf-8")
    sensor_src = (base / "sensor.py").read_text(encoding="utf-8")

    assert 'f"sensor.{DOMAIN}_affected"' in sensor_src
    real = f"sensor.{DOMAIN}_affected"
    assert f"`{real}`" in strings
    # No reference to an entity id that does not exist.
    for line in strings.splitlines():
        for token in line.split("`"):
            if token.startswith("sensor."):
                assert token == real, f"unknown entity id in strings.json: {token}"


def test_every_placeholder_in_strings_is_supplied(repo_root):
    """A missing placeholder renders as a literal {name} in the card."""
    import json
    import re

    from custom_components.breakage_radar.repairs import (
        BROKEN_ISSUE_PREFIX,
        IMMINENT_ISSUE_PREFIX,
    )

    base = repo_root / "custom_components" / "breakage_radar"
    strings = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    repairs_src = (base / "repairs.py").read_text(encoding="utf-8")

    for key, issue in strings["issues"].items():
        used = set(re.findall(r"\{(\w+)\}", issue["title"] + issue["description"]))
        for name in used:
            assert f'"{name}"' in repairs_src, (
                f"issue {key} uses {{{name}}} but repairs.py never supplies it"
            )
    assert BROKEN_ISSUE_PREFIX and IMMINENT_ISSUE_PREFIX
