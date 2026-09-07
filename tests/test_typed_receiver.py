"""The receiver-aware matcher: fire on a proved DeviceEntry, never on hass."""

from __future__ import annotations

import pytest
from conftest import scan_fixture_tree

import tools.rules_engine as engine
from tools.rules_engine import Rule, match_source

RULE_ID = "device-entry-config-entries"

#: The worked example from the deprecation post: two findings, nothing else.
WORKED_EXAMPLE = (
    "from homeassistant.helpers import device_registry as dr\n"
    "\n"
    "async def async_remove_config_entry_device(hass, config_entry, device_entry) -> bool:\n"
    "    return len(device_entry.config_entries) <= 1\n"
    "\n"
    "async def prune(hass, entry):\n"
    "    reg = dr.async_get(hass)\n"
    '    if (device := reg.async_get_device({("x", "y")})):\n'
    "        for other in device.config_entries:\n"
    "            hass.config_entries.async_unload(other)\n"
    "    await hass.config_entries.async_reload(entry.entry_id)\n"
)

#: The same idea for the 2027.9 container rule, used by the shared
#: forward-compatibility test below.
MAPPING_EXAMPLE = (
    "from homeassistant.helpers import device_registry as dr\n"
    "\n"
    "def prune(hass, device_id):\n"
    "    reg = dr.async_get(hass)\n"
    "    return reg.devices[device_id]\n"
)


@pytest.fixture(scope="module")
def rule(shipped_matchable_rules):
    ours = [r for r in shipped_matchable_rules if r.id == RULE_ID]
    assert len(ours) == 1, "the rule must ship matchable"
    return ours[0]


def test_worked_example_yields_exactly_two_findings(rule):
    hits = match_source("custom_components/x/__init__.py", WORKED_EXAMPLE, [rule])
    assert [f.to_dict() for f in hits] == [
        {
            "rule_id": RULE_ID,
            "breaks_in": "2027.8",
            "file": "custom_components/x/__init__.py",
            "line": 4,
            "confidence": "high",
        },
        {
            "rule_id": RULE_ID,
            "breaks_in": "2027.8",
            "file": "custom_components/x/__init__.py",
            "line": 9,
            "confidence": "high",
        },
    ]


def test_every_proved_receiver_shape_fires(fixtures_dir, rule):
    findings = scan_fixture_tree(
        fixtures_dir / "typed_receiver" / "true_positive", [rule]
    )
    assert [f.line for f in findings] == [13, 20, 27, 33, 36, 43, 51, 64, 72, 75]


def test_a_proof_does_not_escape_the_scope_that_earned_it(rule):
    """`device` is one of the commonest locals in this ecosystem, so proving
    names file-wide would flag one that came out of a dict."""
    sibling_function = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def real(hass):\n"
        "    reg = dr.async_get(hass)\n"
        '    device = reg.async_get_device({("x", "y")})\n'
        "    return device.config_entries\n"
        "\n"
        "def unrelated(payload):\n"
        '    device = payload["device"]\n'
        "    return device.config_entries\n"
    )
    shadowing_parameter = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        'device = dr.async_get(HASS).async_get_device({("x", "y")})\n'
        "\n"
        "def go(device):\n"
        "    return device.config_entries\n"
    )
    assert [
        f.line for f in match_source("custom_components/x/a.py", sibling_function, [rule])
    ] == [6]
    assert match_source("custom_components/x/b.py", shadowing_parameter, [rule]) == []


#: Every receiver-aware matcher type, with source that exercises it. Each was
#: new once, and each has to be invisible to the engine version before it.
NEWER_THAN_SOME_INSTALL = [
    ("attr_access_typed", "device-entry-config-entries", WORKED_EXAMPLE),
    ("container_use", "device-registry-devices-mapping", MAPPING_EXAMPLE),
]


@pytest.mark.parametrize(
    ("matcher_type", "rule_id", "source"), NEWER_THAN_SOME_INSTALL
)
def test_an_old_engine_silently_skips_a_newer_type(
    monkeypatch, shipped_matchable_rules, matcher_type, rule_id, source
):
    """A 1.4.1 install reads the same published index with the old engine
    vendored. An unknown matcher type has to be invisible there, where an
    unknown key on `attr_access` would have fired on every hass.config_entries
    in the world."""
    monkeypatch.setattr(
        engine, "MATCHER_TYPES", engine.MATCHER_TYPES - {matcher_type}
    )
    monkeypatch.setattr(
        engine,
        "_DISPATCH",
        {k: v for k, v in engine._DISPATCH.items() if k != matcher_type},
    )
    shipped = next(r for r in shipped_matchable_rules if r.id == rule_id)
    old_rule = Rule.from_dict(shipped.to_dict())
    assert old_rule.matchable is False
    assert engine.match_source("custom_components/x/a.py", source, [old_rule]) == []
