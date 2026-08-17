"""The receiver-aware matcher: fire on a proved DeviceEntry, never on hass."""

from __future__ import annotations

import json

import pytest

import tools.rules_engine as engine
from tools.rules_engine import Rule, load_rules, match_source, matchable_rules

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


def test_every_proved_receiver_shape_fires(fixtures_dir, rule):
    findings = _scan_tree(fixtures_dir / "typed_receiver" / "true_positive", [rule])
    assert [f["line"] for f in findings] == [13, 20, 27, 33, 36, 43, 51, 64, 72, 75]


def test_lookalikes_produce_zero_findings_under_every_rule(fixtures_dir, rules):
    assert _scan_tree(fixtures_dir / "typed_receiver" / "false_positive", rules) == []


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


def test_an_old_engine_silently_skips_the_new_type(monkeypatch, rule):
    """A 1.4.1 install reads the same published index with the old engine
    vendored. An unknown matcher type has to be invisible there, where an
    unknown key on `attr_access` would have fired on every hass.config_entries
    in the world."""
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
    assert engine.match_source("custom_components/x/a.py", WORKED_EXAMPLE, [old_rule]) == []
