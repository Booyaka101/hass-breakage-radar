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
            "breaks_in": "2027.10",
            "file": "custom_components/x/__init__.py",
            "line": 4,
            "confidence": "high",
        },
        {
            "rule_id": RULE_ID,
            "breaks_in": "2027.10",
            "file": "custom_components/x/__init__.py",
            "line": 9,
            "confidence": "high",
        },
    ]


def test_every_proved_receiver_shape_fires(fixtures_dir, rule):
    findings = scan_fixture_tree(
        fixtures_dir / "typed_receiver" / "true_positive", [rule]
    )
    assert [f.line for f in findings] == [
        13, 20, 27, 33, 36, 43, 51, 64, 72, 75, 80, 87, 90, 91
    ]


def test_a_deleted_or_child_device_is_a_proved_receiver_too(rule):
    """The 15 September post: the properties on `DeletedDeviceEntry` "are
    deprecated and report on the same terms", and `ChildDeviceEntry` inherits
    them. Neither is ever a composite, so neither read has the exemption a
    `DeviceEntry` read can have."""
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "from homeassistant.helpers.device_registry import ChildDeviceEntry\n"
        "\n"
        "def orphaned(deleted: dr.DeletedDeviceEntry):\n"
        "    return deleted.config_entries\n"
        "\n"
        "def inherited(child: ChildDeviceEntry):\n"
        "    return child.config_entries\n"
        "\n"
        "def looked_up(hass, config_entry):\n"
        "    reg = dr.async_get(hass)\n"
        "    for child in dr.async_child_entries_for_config_entry(reg, config_entry.entry_id):\n"
        "        return child.config_entries\n"
        "    return None\n"
    )
    hits = match_source("custom_components/x/__init__.py", source, [rule])
    assert [f.line for f in hits] == [5, 8, 13]


def test_every_child_device_lookup_proves_its_result(rule):
    """The four remaining ways core 2026.9 hands back a `ChildDeviceEntry`.

    A type list only covers annotations, so each lookup has to be named
    separately or the classes are matched where nobody writes them.
    """
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def updated(hass, device_id):\n"
        "    return dr.async_get(hass).async_update_child_device(device_id).config_entries\n"
        "\n"
        "def by_identifier(hass, config_entry):\n"
        "    reg = dr.async_get(hass)\n"
        "    child = reg.async_get_child_device_by_identifier(ident, config_entry.entry_id)\n"
        "    return child.config_entries\n"
        "\n"
        "def created(hass, config_entry, device_id):\n"
        "    reg = dr.async_get(hass)\n"
        "    child = reg.async_get_or_create_child(\n"
        "        config_entry_id=config_entry.entry_id, parent_device_id=device_id\n"
        "    )\n"
        "    return child.config_entries\n"
        "\n"
        "def of_a_parent(hass, device_id):\n"
        "    reg = dr.async_get(hass)\n"
        "    return [c.config_entries for c in dr.async_entries_for_parent_device(reg, device_id)]\n"
    )
    hits = match_source("custom_components/x/__init__.py", source, [rule])
    assert [f.line for f in hits] == [4, 9, 16, 20]


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
