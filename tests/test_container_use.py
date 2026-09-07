"""The 2027.9 device-registry container rules.

`registry.devices` is not deprecated -- using it as a mapping is. The matcher
has to separate the two on the same attribute, on a receiver it has proved.
"""

from __future__ import annotations

import ast

import pytest
from conftest import scan_fixture_tree

from tools.rules_engine import _module_in_reach, build_import_map, match_source

MAPPING = "device-registry-devices-mapping"
DELETED = "device-registry-deleted-devices"

#: The worked examples from the 2026-08-24 post, in one file that proves the
#: receiver the way real integrations do.
WORKED_EXAMPLE = (
    "from homeassistant.helpers import device_registry as dr\n"
    "\n"
    "def prune(hass, device_id):\n"
    "    reg = dr.async_get(hass)\n"
    "    entry = reg.devices[device_id]\n"
    "    found = reg.devices.get(device_id)\n"
    "    for other in reg.devices.values():\n"
    "        entry = entry or other\n"
    "    if device_id in reg.devices:\n"
    "        return entry, found, reg.deleted_devices\n"
    "    return None\n"
)


def _symbols(source, rules, path="custom_components/x/__init__.py"):
    return [(f.line, f.rule_id, f.symbol) for f in match_source(path, source, rules)]


@pytest.fixture(scope="module")
def container_rules(shipped_matchable_rules):
    ours = [r for r in shipped_matchable_rules if r.id in (MAPPING, DELETED)]
    assert {r.id for r in ours} == {MAPPING, DELETED}, "both rules must ship matchable"
    return ours


def test_the_worked_example_names_each_deprecated_use(container_rules):
    assert _symbols(WORKED_EXAMPLE, container_rules) == [
        (5, MAPPING, "devices[...]"),
        (6, MAPPING, "devices.get"),
        (7, MAPPING, "devices.values"),
        (9, MAPPING, "devices in"),
        (10, DELETED, "deleted_devices"),
    ]


def test_every_deprecated_container_use_fires(fixtures_dir, container_rules):
    findings = scan_fixture_tree(
        fixtures_dir / "registry_container" / "true_positive", container_rules
    )
    assert [(f.line, f.symbol) for f in findings] == [
        (14, "devices[...]"),
        (15, "devices.get"),
        (16, "devices.values"),
        (18, "devices in"),
        (26, "deleted_devices"),
        (27, "deleted_devices"),
        (54, "devices.keys"),
        (57, "devices[...]"),
    ]


def test_the_deprecated_device_info_keywords_fire(fixtures_dir, shipped_matchable_rules):
    findings = scan_fixture_tree(
        fixtures_dir / "registry_container" / "true_positive", shipped_matchable_rules
    )
    assert sorted(
        (f.line, f.rule_id) for f in findings if f.rule_id.startswith("device-info-")
    ) == [
        (33, "device-info-created-at"),
        (33, "device-info-default-manufacturer"),
        (33, "device-info-default-name"),
        (40, "device-info-default-model"),
        (40, "device-info-modified-at"),
    ]


SUPPORTED = (
    "from homeassistant.helpers import device_registry as dr\n"
    "\n"
    "def go(hass, sink, device_entry):\n"
    "    reg = dr.async_get(hass)\n"
    "    for device in reg.devices:\n"
    "        sink(device)\n"
    "    sink(reg.devices)\n"
    "    return len(reg.devices), list(reg.devices), device_entry in reg.devices\n"
)


def test_the_supported_uses_of_the_same_attribute_stay_silent(container_rules):
    """`__iter__` and `__len__` do not call report_usage, and value membership
    is the supported test, so none of this breaks in 2027.9."""
    assert _symbols(SUPPORTED, container_rules) == []


@pytest.mark.parametrize(
    ("operand", "fires"),
    [
        ('"abc123"', True),
        ("device_id", True),
        ("entry_id", True),
        ("entry.device_id", True),
        ("wanted", False),
        ("device_entry", False),
        ("self.entry", False),
    ],
)
def test_membership_fires_only_on_something_spelled_like_a_device_id(
    container_rules, operand, fires
):
    """Core reports string membership only. Nothing in one file proves the
    type, so the spelling decides, and undercounting is the safe side."""
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def go(hass, device_id, entry_id, wanted, device_entry, entry, self):\n"
        "    reg = dr.async_get(hass)\n"
        f"    return {operand} in reg.devices\n"
    )
    assert bool(_symbols(source, container_rules)) is fires


def test_an_unproved_receiver_is_never_flagged(container_rules):
    """`devices` is a field on half the coordinators in the ecosystem."""
    ours = (
        "from .registry import async_get\n"
        "\n"
        "devices = {}\n"
        "\n"
        "class Coordinator:\n"
        "    def go(self, hass, key):\n"
        "        return self.coordinator.devices[key], devices[key]\n"
        "\n"
        "def relative(hass, key):\n"
        "    return async_get(hass).devices[key]\n"
    )
    assert _symbols(ours, container_rules) == []


def test_a_mapping_read_of_a_deprecated_field_is_two_breakages(
    shipped_matchable_rules,
):
    """`reg.devices[x].config_entries` breaks twice, in 2027.9 and in 2027.8.
    They are two separate migrations, so both are reported."""
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def go(hass, device_id):\n"
        "    return dr.async_get(hass).devices[device_id].config_entries\n"
    )
    assert sorted(
        (f.rule_id, f.breaks_in) for f in match_source("x.py", source, shipped_matchable_rules)
    ) == [
        ("device-entry-config-entries", "2027.8"),
        (MAPPING, "2027.9"),
    ]


def test_the_superseded_core_prose_rules_are_gone(shipped_rules):
    """The hand-written matchers replace core's own prose, which said the same
    thing with no matcher and no advice."""
    published = {rule["id"] for rule in shipped_rules["rules"]}
    manual = {rule["id"]: rule for rule in shipped_rules["rules"]}
    claimed = {
        superseded
        for rule in manual.values()
        for superseded in rule.get("supersedes", ())
    }
    assert claimed, "the container rules must declare what they supersede"
    assert claimed.isdisjoint(published)


def test_a_matcher_with_no_container_named_matches_nothing():
    """A rule written against a future engine must not fire on everything."""
    from tools.rules_engine import Rule

    rule = Rule(
        id="broken",
        kind="attr",
        symbol="x",
        message="x",
        breaks_in="2027.9",
        source="test",
        match={"type": "container_use", "uses": ["any"]},
    )
    assert match_source("x.py", WORKED_EXAMPLE, [rule]) == []


@pytest.mark.parametrize(
    ("import_line", "factory"),
    [
        ("from homeassistant.helpers import device_registry as dr", "dr.async_get"),
        ("from homeassistant.helpers.device_registry import async_get", "async_get"),
        ("import homeassistant.helpers.device_registry as dr", "dr.async_get"),
        (
            "import homeassistant.helpers.device_registry",
            "homeassistant.helpers.device_registry.async_get",
        ),
    ],
)
def test_every_way_of_importing_the_registry_reaches_the_matcher(
    container_rules, import_line, factory
):
    """The matcher skips a file whose imports cannot reach the device registry,
    so every spelling of that import has to keep working."""
    source = (
        f"{import_line}\n"
        "\n"
        "def go(hass, device_id):\n"
        f"    return {factory}(hass).devices[device_id]\n"
    )
    assert [f.symbol for f in match_source("x.py", source, container_rules)] == [
        "devices[...]"
    ]


def test_a_file_that_cannot_reach_the_device_registry_is_skipped(container_rules):
    """The early-out must never skip a file that could still match."""
    matcher = next(r.match for r in container_rules if r.id == MAPPING)
    unrelated = build_import_map(
        ast.parse("from homeassistant.core import HomeAssistant\n")
    )
    reaching = build_import_map(
        ast.parse("from homeassistant.helpers import device_registry as dr\n")
    )
    assert _module_in_reach(matcher, unrelated) is False
    assert _module_in_reach(matcher, reaching) is True
