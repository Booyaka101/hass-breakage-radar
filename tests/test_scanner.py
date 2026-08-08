"""The worked example: one true positive, zero false positives."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from tools.rules_engine import (
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
