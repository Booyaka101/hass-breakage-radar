"""The worked example: one true positive, zero false positives."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from tools.rules_engine import (
    Rule,
    ScanStats,
    load_rules,
    match_source,
    matchable_rules,
)
from tools.scan import candidate_refs, iter_component_python, iter_manifest_domains


@pytest.fixture(scope="module")
def rules(request):
    """The shipped rule set, restricted to what can actually be matched."""
    path = request.config.rootpath / "data" / "rules.json"
    if not path.exists():
        pytest.skip("data/rules.json not built yet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return matchable_rules(
        load_rules(payload["rules"]), current_version=payload["core_version"]
    )


def _scan_tree(root: Path, rules) -> list[dict]:
    findings: list[dict] = []
    stats = ScanStats()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        findings.extend(
            f.to_dict()
            for f in match_source(relative, path.read_bytes(), rules, stats)
        )
    return findings


def test_true_positive_produces_exactly_one_finding(fixtures_dir, rules):
    findings = _scan_tree(fixtures_dir / "true_positive", rules)
    assert findings == [
        {
            "rule_id": "legacy-device-tracker-platform",
            "breaks_in": "2027.5",
            "file": "custom_components/fixture_tracker/device_tracker.py",
            "line": 12,
            "confidence": "high",
        }
    ]


def test_lookalikes_produce_zero_findings(fixtures_dir, rules):
    assert _scan_tree(fixtures_dir / "false_positive", rules) == []


def test_setup_scanner_in_a_class_body_is_not_a_platform(rules):
    source = (
        "class Thing:\n"
        "    def setup_scanner(self, hass, config, see):\n"
        "        return True\n"
    )
    assert match_source("custom_components/x/device_tracker.py", source, rules) == []


def test_setup_scanner_in_the_wrong_file_is_not_a_platform(rules):
    source = "def setup_scanner(hass, config, see):\n    return True\n"
    assert match_source("custom_components/x/sensor.py", source, rules) == []
    hits = match_source("custom_components/x/device_tracker.py", source, rules)
    assert [f.rule_id for f in hits] == ["legacy-device-tracker-platform"]


def test_device_scanner_subclass_is_flagged(rules):
    source = (
        "from homeassistant.components.device_tracker import DeviceScanner\n"
        "\n"
        "\n"
        "class MyScanner(DeviceScanner):\n"
        "    def scan_devices(self):\n"
        "        return []\n"
    )
    hits = match_source("custom_components/x/device_tracker.py", source, rules)
    assert [f.rule_id for f in hits] == ["legacy-device-tracker-scanner-class"]
    assert hits[0].line == 4


def test_battery_level_only_fires_on_a_tracker_base_class(rules):
    tracker = (
        "from homeassistant.components.device_tracker import TrackerEntity\n"
        "\n"
        "\n"
        "class T(TrackerEntity):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return 1\n"
    )
    plain = (
        "class T:\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return 1\n"
    )
    assert [f.rule_id for f in match_source("custom_components/x/device_tracker.py", tracker, rules)] == [
        "device-tracker-battery-level"
    ]
    assert match_source("custom_components/x/device_tracker.py", plain, rules) == []


def test_import_resolution_separates_same_named_functions(rules):
    """The real false positive found on 0xAlon/dolphin during the first crawl."""
    healthy = (
        "from homeassistant.helpers.entity import async_generate_entity_id\n"
        "\n"
        "\n"
        "def go(hass):\n"
        "    return async_generate_entity_id('x.{}', 'y', hass=hass)\n"
    )
    deprecated = (
        "from homeassistant.helpers.entity_registry import async_generate_entity_id\n"
        "\n"
        "\n"
        "def go(hass):\n"
        "    return async_generate_entity_id('x.{}', 'y', hass=hass)\n"
    )
    assert match_source("custom_components/x/sensor.py", healthy, rules) == []
    assert [
        f.rule_id for f in match_source("custom_components/x/sensor.py", deprecated, rules)
    ] == ["core-call-async-generate-entity-id"]


def test_aliased_module_import_still_resolves(rules):
    source = (
        "from homeassistant.helpers import entity_registry as er\n"
        "\n"
        "\n"
        "def go(hass):\n"
        "    return er.async_generate_entity_id('x.{}', 'y', hass=hass)\n"
    )
    assert [
        f.rule_id for f in match_source("custom_components/x/sensor.py", source, rules)
    ] == ["core-call-async-generate-entity-id"]


def test_syntax_error_is_survivable(rules):
    stats = ScanStats()
    assert match_source("custom_components/x/broken.py", "def (:\n", rules, stats) == []
    assert len(stats.syntax_errors) == 1
    assert stats.files_scanned == 0


def test_undecodable_bytes_do_not_raise(rules):
    stats = ScanStats()
    assert match_source("custom_components/x/b.py", b"\xff\xfe\x00bad", rules, stats) == []


def test_candidate_refs_order():
    assert candidate_refs("1.2.3") == [
        "refs/tags/1.2.3",
        "refs/tags/v1.2.3",
        "refs/heads/main",
        "refs/heads/master",
    ]
    assert candidate_refs("") == ["refs/heads/main", "refs/heads/master"]
    assert candidate_refs("v2.0")[:2] == ["refs/tags/v2.0", "refs/tags/2.0"]


def _make_tarball(tmp_path: Path, files: dict[str, str]) -> bytes:
    archive_path = tmp_path / "repo.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, content in files.items():
            member_path = tmp_path / "staging" / name
            member_path.parent.mkdir(parents=True, exist_ok=True)
            member_path.write_text(content, encoding="utf-8")
            archive.add(member_path, arcname=f"repo-1.0/{name}")
    return archive_path.read_bytes()


def test_tarball_reader_skips_vendored_and_non_component_python(tmp_path):
    body = _make_tarball(
        tmp_path,
        {
            "custom_components/demo/__init__.py": "X = 1\n",
            "custom_components/demo/vendor/lib.py": "Y = 2\n",
            "scripts/build.py": "Z = 3\n",
            "custom_components/demo/manifest.json": '{"domain": "demo"}',
        },
    )
    paths = [path for path, _ in iter_component_python(body)]
    assert paths == ["custom_components/demo/__init__.py"]
    assert iter_manifest_domains(body) == ["demo"]


def test_repo_without_custom_components_yields_nothing(tmp_path):
    body = _make_tarball(tmp_path, {"README.md": "hi", "setup.py": "pass\n"})
    assert list(iter_component_python(body)) == []
    assert iter_manifest_domains(body) == []


def test_deprecated_hass_argument_only_fires_when_hass_is_passed(rules):
    """The `hass` first argument is deprecated, not the function.

    AlexxIT/YandexStation does both on consecutive lines, which is how this
    false positive was found.
    """
    source = (
        "from homeassistant.helpers import service\n"
        "\n"
        "\n"
        "async def go(hass, call):\n"
        "    a = await service.async_extract_entity_ids(call)\n"
        "    b = await service.async_extract_entity_ids(hass, call)\n"
        "    c = await service.async_extract_entity_ids(hass=hass, service_call=call)\n"
        "    return a, b, c\n"
    )
    hits = match_source("custom_components/x/__init__.py", source, rules)
    assert [(f.rule_id, f.line) for f in hits] == [
        ("core-call-async-extract-entity-ids", 6),
        ("core-call-async-extract-entity-ids", 7),
    ]


def test_verify_domain_control_decorator_without_hass_is_clean(rules):
    source = (
        "from homeassistant.helpers.service import verify_domain_control\n"
        "\n"
        "\n"
        "@verify_domain_control('mydomain')\n"
        "async def handler(call):\n"
        "    return None\n"
    )
    assert match_source("custom_components/x/services.py", source, rules) == []


def test_awaited_async_get_device_is_somebody_elses_method(rules):
    """DeviceRegistry.async_get_device is a plain def in core, so a call that
    is awaited cannot be it.

    Measured on ThomasLomas/ha-starlinghomehub, which awaits its own API
    client's method of the same name. That was the only false positive in 45
    hand-checked hits of this rule.
    """
    source = (
        "from homeassistant.helpers import device_registry as dr\n"
        "\n"
        "\n"
        "async def refresh(self, hass, device):\n"
        "    theirs = await self.client.async_get_device(device_id=device['id'])\n"
        "    registry = dr.async_get(hass)\n"
        "    ours = registry.async_get_device(identifiers={('x', 'y')})\n"
        "    return theirs, ours\n"
    )
    hits = [
        f for f in match_source("custom_components/x/coordinator.py", source, rules)
        if f.rule_id == "device-registry-async-get-device"
    ]
    assert [f.line for f in hits] == [7]


# --------------------------------------------------------------------------- #
# scoped attr matchers -- the short symbol only a base class makes safe
# --------------------------------------------------------------------------- #

VACUUM_RULE = Rule(
    id="core-attr-statevacuumentity-battery-level",
    kind="attr",
    symbol="StateVacuumEntity.battery_level",
    message="defines `battery_level` on a subclass of `StateVacuumEntity`.",
    breaks_in="2026.9",
    source="homeassistant/components/vacuum/__init__.py:269",
    confidence="medium",
    match={
        "type": "attr",
        "names": ["battery_level"],
        "in_class_base": ["StateVacuumEntity"],
    },
)


def _vacuum_hits(source: str) -> list[int]:
    return [
        f.line for f in match_source("custom_components/x/vacuum.py", source, [VACUUM_RULE])
    ]


def test_scoped_rule_fires_on_the_inheriting_class(fixtures_dir):
    path = (
        fixtures_dir
        / "scoped_attr"
        / "custom_components"
        / "fixture_vacuum"
        / "vacuum.py"
    )
    findings = match_source(
        "custom_components/fixture_vacuum/vacuum.py", path.read_bytes(), [VACUUM_RULE]
    )
    assert [(f.line, f.symbol) for f in findings] == [(29, "battery_level")]


def test_scoped_rule_ignores_module_level_and_unrelated_classes():
    source = (
        "battery_level = 50\n"
        "\n"
        "\n"
        "class Foo:\n"
        "    battery_level = 50\n"
        "\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return 50\n"
    )
    assert _vacuum_hits(source) == []


def test_a_base_imported_under_an_alias_still_resolves():
    source = (
        "from homeassistant.components.vacuum import StateVacuumEntity as Base\n"
        "\n"
        "\n"
        "class MyVacuum(Base):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return self._batt\n"
    )
    assert _vacuum_hits(source) == [6]


def test_a_dotted_base_still_resolves():
    source = (
        "from homeassistant.components import vacuum\n"
        "\n"
        "\n"
        "class MyVacuum(vacuum.StateVacuumEntity):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return self._batt\n"
    )
    assert _vacuum_hits(source) == [6]


def test_multiple_inheritance_matches_on_any_base():
    source = (
        "from homeassistant.components.vacuum import StateVacuumEntity\n"
        "from homeassistant.helpers.update_coordinator import CoordinatorEntity\n"
        "\n"
        "\n"
        "class MyVacuum(CoordinatorEntity, StateVacuumEntity):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return self._batt\n"
    )
    assert _vacuum_hits(source) == [7]


def test_a_subclass_of_a_subclass_resolves_within_the_file():
    source = (
        "from homeassistant.components.vacuum import StateVacuumEntity\n"
        "\n"
        "\n"
        "class BaseVacuum(StateVacuumEntity):\n"
        "    pass\n"
        "\n"
        "\n"
        "class MyVacuum(BaseVacuum):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return self._batt\n"
    )
    assert _vacuum_hits(source) == [10]


def test_a_chain_that_leaves_the_file_is_not_guessed_at():
    """Undercount rather than false-positive: ``.base`` could be anything."""
    source = (
        "from .base import BaseVacuum\n"
        "\n"
        "\n"
        "class MyVacuum(BaseVacuum):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return self._batt\n"
    )
    assert _vacuum_hits(source) == []


def test_an_attribute_assigned_in_init_is_reported():
    source = (
        "from homeassistant.components.vacuum import StateVacuumEntity\n"
        "\n"
        "\n"
        "class MyVacuum(StateVacuumEntity):\n"
        "    def __init__(self, batt):\n"
        "        self._attr_battery_level = batt\n"
    )
    assert _vacuum_hits(source) == [6]


def test_the_same_symbol_on_two_base_classes_stays_two_rules():
    tracker = Rule(
        id="device-tracker-battery-level",
        kind="attr",
        symbol="TrackerEntity.battery_level",
        message="defines `battery_level` on a subclass of `TrackerEntity`.",
        breaks_in="2027.7",
        source="https://developers.home-assistant.io/blog/",
        confidence="high",
        match={
            "type": "attr",
            "names": ["battery_level"],
            "in_class_base": ["TrackerEntity"],
        },
    )
    source = (
        "from homeassistant.components.vacuum import StateVacuumEntity\n"
        "from homeassistant.components.device_tracker import TrackerEntity\n"
        "\n"
        "\n"
        "class MyVacuum(StateVacuumEntity):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "class MyTracker(TrackerEntity):\n"
        "    @property\n"
        "    def battery_level(self):\n"
        "        return 2\n"
    )
    findings = match_source(
        "custom_components/x/vacuum.py", source, [VACUUM_RULE, tracker]
    )
    assert sorted((f.rule_id, f.line) for f in findings) == [
        ("core-attr-statevacuumentity-battery-level", 7),
        ("device-tracker-battery-level", 13),
    ]


def test_a_call_pinned_to_a_class_resolves_through_the_import():
    rule = Rule(
        id="core-call-temperatureconverter-convert-interval",
        kind="call",
        symbol="TemperatureConverter.convert_interval",
        message="deprecated",
        breaks_in="2026.12",
        source="homeassistant/util/unit_conversion.py:874",
        match={
            "type": "call",
            "names": ["convert_interval"],
            "modules": ["homeassistant.util.unit_conversion.TemperatureConverter"],
        },
    )
    source = (
        "from homeassistant.util.unit_conversion import TemperatureConverter\n"
        "\n"
        "\n"
        "class Mine:\n"
        "    def convert_interval(self, x):\n"
        "        return x\n"
        "\n"
        "\n"
        "def f(self, x):\n"
        "    a = TemperatureConverter.convert_interval(x, 'K', 'C')\n"
        "    b = self.convert_interval(x)\n"
        "    return a, b\n"
    )
    findings = match_source("custom_components/x/sensor.py", source, [rule])
    assert [f.line for f in findings] == [10]


def test_a_short_pinned_call_never_fires_on_a_local_helper():
    rule = Rule(
        id="core-call-is-closed",
        kind="call",
        symbol="is_closed",
        message="deprecated",
        breaks_in="2027.10",
        source="homeassistant/components/cover/__init__.py:95",
        match={
            "type": "call",
            "names": ["is_closed"],
            "modules": ["homeassistant.components.cover"],
        },
    )
    theirs = (
        "from homeassistant.components.cover import is_closed\n"
        "\n"
        "\n"
        "def check(hass, eid):\n"
        "    return is_closed(hass, eid)\n"
    )
    ours = (
        "from .helpers import is_closed\n"
        "\n"
        "\n"
        "def check(hass, eid):\n"
        "    return is_closed(hass, eid) or hass.states.is_closed(eid)\n"
    )
    assert [f.line for f in match_source("custom_components/x/a.py", theirs, [rule])] == [5]
    assert match_source("custom_components/x/b.py", ours, [rule]) == []
