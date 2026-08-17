"""The receiver-aware matcher: fire on a proven DeviceEntry, never on hass."""

from __future__ import annotations

import json

import pytest

import tools.rules_engine as engine
from tools.rules_engine import (
    Rule,
    load_rules,
    match_source,
    matchable_rules,
)

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


@pytest.fixture(scope="module")
def rules(request):
    path = request.config.rootpath / "data" / "rules.json"
    if not path.exists():
        pytest.skip("data/rules.json not built yet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return matchable_rules(
        load_rules(payload["rules"]), current_version=payload["core_version"]
    )


@pytest.fixture(scope="module")
def rule(rules):
    """The shipped device-entry-config-entries rule, matcher included."""
    ours = [r for r in rules if r.id == RULE_ID]
    assert len(ours) == 1, "the rule must ship matchable"
    return ours[0]


def _scan_tree(root, rules) -> list[dict]:
    findings = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        findings.extend(
            f.to_dict() for f in match_source(relative, path.read_bytes(), rules)
        )
    return findings


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


def test_every_proven_receiver_shape_fires(fixtures_dir, rule):
    findings = _scan_tree(fixtures_dir / "typed_receiver" / "true_positive", [rule])
    assert [f["line"] for f in findings] == [14, 21, 28, 29, 36, 39, 44]


def test_lookalikes_produce_zero_findings_under_every_rule(fixtures_dir, rules):
    """hass.config_entries and friends, scanned with the full shipped rule set."""
    assert _scan_tree(fixtures_dir / "typed_receiver" / "false_positive", rules) == []


def test_an_old_engine_silently_skips_the_new_type(monkeypatch, rule):
    """A 1.4.1 install reads the same published index. Its engine has no
    attr_access_typed handler, so the rule must be invisible there: not
    matchable, and zero findings from match_source."""
    monkeypatch.setattr(
        engine, "MATCHER_TYPES", engine.MATCHER_TYPES - {"attr_access_typed"}
    )
    monkeypatch.setattr(
        engine,
        "_DISPATCH",
        {k: v for k, v in engine._DISPATCH.items() if k != "attr_access_typed"},
    )
    old_rule = Rule.from_dict(rule.to_dict())
    assert old_rule.matchable is False
    assert (
        engine.match_source("custom_components/x/__init__.py", WORKED_EXAMPLE, [old_rule])
        == []
    )


def test_bare_unimported_async_get_proves_nothing(rule):
    source = (
        "def go(hass):\n"
        "    reg = async_get(hass)\n"
        '    device = reg.async_get_device({("x", "y")})\n'
        "    return device.config_entries\n"
    )
    assert match_source("custom_components/x/__init__.py", source, [rule]) == []


def test_contract_only_binds_a_module_level_async_def(rule):
    method = (
        "class Thing:\n"
        "    async def async_remove_config_entry_device(self, hass, entry, device):\n"
        "        return device.config_entries\n"
    )
    plain = (
        "def async_remove_config_entry_device(hass, entry, device):\n"
        "    return device.config_entries\n"
    )
    assert match_source("custom_components/x/__init__.py", method, [rule]) == []
    assert match_source("custom_components/x/__init__.py", plain, [rule]) == []


def test_string_and_optional_annotations_still_bind(rule):
    source = (
        "from typing import Optional\n"
        "from homeassistant.helpers.device_registry import DeviceEntry\n"
        "\n"
        'def a(device: "DeviceEntry"):\n'
        "    return device.config_entries\n"
        "\n"
        "def b(device: Optional[DeviceEntry]):\n"
        "    return device.config_entries\n"
    )
    hits = match_source("custom_components/x/__init__.py", source, [rule])
    assert [f.line for f in hits] == [5, 8]


def test_entry_function_from_another_module_does_not_bind(rule):
    source = (
        "from .helpers import async_entries_for_config_entry\n"
        "\n"
        "def go(reg, entry_id):\n"
        "    for device in async_entries_for_config_entry(reg, entry_id):\n"
        "        return device.config_entries\n"
    )
    assert match_source("custom_components/x/__init__.py", source, [rule]) == []


def test_a_receiver_the_binder_cannot_model_stays_quiet(rule):
    """Reading the registry's own dict is a real DeviceEntry, and is missed.

    `blue_current` does this in core. Pinned deliberately: the boundary is
    under-reporting by design, so a future change that starts guessing at
    unmodelled receiver shapes has to come past this test.
    """
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def go(hass, device_id):\n"
        "    device = dr.async_get(hass).devices.get(device_id)\n"
        "    return device.config_entries\n"
    )
    assert match_source("custom_components/x/__init__.py", source, [rule]) == []


def test_registry_annotation_alone_proves_the_chain(rule):
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "def go(registry: dr.DeviceRegistry):\n"
        '    device = registry.async_get_device({("x", "y")})\n'
        "    return device.config_entries\n"
    )
    hits = match_source("custom_components/x/__init__.py", source, [rule])
    assert [f.line for f in hits] == [5]
