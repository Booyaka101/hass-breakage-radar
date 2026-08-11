"""The Home Assistant side: report building, the sensor and the repairs issue.

These exercise the shipped classes. When Home Assistant is not installed,
``tests/conftest.py`` registers minimal stand-ins for the handful of symbols the
integration imports, so this is the real ``BreakageRadarSensor`` -- not a copy of
its logic.
"""

from __future__ import annotations

import json
import re

import pytest

from custom_components.breakage_radar.const import ATTR_BY_RELEASE, MAX_DETAILS
from custom_components.breakage_radar.report import (
    build_report,
    discover_installed,
    validate_index,
)
from custom_components.breakage_radar.sensor import BreakageRadarSensor


@pytest.fixture
def sample_index(fixtures_dir):
    return json.loads(
        (fixtures_dir / "index_sample.json").read_text(encoding="utf-8")
    )


class FakeCoordinator:
    """Stands in for the DataUpdateCoordinator; the sensor only reads .data."""

    def __init__(self, data, *, success=True, error=None):
        self.data = data
        self.last_update_success = success
        self.last_error = error
        self.index_url = "https://example.invalid/index.json"


# --------------------------------------------------------------------------- #
# discover_installed
# --------------------------------------------------------------------------- #


def test_discover_installed_reads_domain_and_version(tmp_path):
    # Built here rather than committed: hacs/default's validation walks the whole
    # repository for `*manifest.json` and requires exactly one, so a fixture
    # manifest on disk breaks HACS inclusion.
    component = tmp_path / "fixture_tracker"
    component.mkdir()
    (component / "manifest.json").write_text(
        json.dumps({"domain": "fixture_tracker", "version": "0.1.0"}), encoding="utf-8"
    )
    assert discover_installed(str(tmp_path)) == {"fixture_tracker": "0.1.0"}


def test_discover_installed_falls_back_to_the_directory_name(fixtures_dir):
    """A component directory with no manifest still counts as installed."""
    installed = discover_installed(
        str(fixtures_dir / "true_positive" / "custom_components")
    )
    assert installed == {"fixture_tracker": ""}


def test_manifest_json_is_unique_in_the_repository(repo_root):
    """hacs/default rejects a repository containing more than one manifest.json."""
    found = [
        p.relative_to(repo_root).as_posix()
        for p in repo_root.rglob("*manifest.json")
        if ".cache" not in p.parts and "build" not in p.parts
    ]
    assert found == ["custom_components/breakage_radar/manifest.json"], found


def test_pages_root_holds_only_published_artifacts(repo_root):
    """``docs/`` is the GitHub Pages root, not a documentation folder.

    Anything committed there is served publicly, so prose belongs in
    ``guides/``. Keeping the Pages root to exactly the published artifacts also
    means a stray file can never shadow the board or the index.
    """
    published = sorted(p.name for p in (repo_root / "docs").iterdir())
    assert published == [".nojekyll", "index.html", "index.json"], published


def test_the_author_guide_is_reachable_from_the_readme(repo_root):
    guide = repo_root / "guides" / "for-integration-authors.md"
    assert guide.exists()
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "guides/for-integration-authors.md" in readme
    # The guide links back to a section that has to still exist.
    assert "../README.md#contributing-a-rule" in guide.read_text(encoding="utf-8")
    assert "## Contributing a rule" in readme


def test_discover_installed_on_a_missing_directory_is_empty(tmp_path):
    assert discover_installed(str(tmp_path / "nope")) == {}


def test_discover_installed_survives_a_broken_manifest(tmp_path):
    component = tmp_path / "broken_thing"
    component.mkdir()
    (component / "manifest.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    assert discover_installed(str(tmp_path)) == {"broken_thing": ""}


# --------------------------------------------------------------------------- #
# build_report -- the worked example
# --------------------------------------------------------------------------- #


def test_worked_example_report(sample_index):
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    assert report["affected_count"] == 1
    assert report["by_release"] == {"2027.5": ["fixture_tracker"]}
    assert report["details"] == [
        {
            "domain": "fixture_tracker",
            "rule_id": "legacy-device-tracker-platform",
            "breaks_in": "2027.5",
            "file": "custom_components/fixture_tracker/device_tracker.py",
            "line": 12,
            "confidence": "high",
            "source": "index",
            "when": "upcoming",
            "repository": "example/fixture-tracker",
            "scanned_version": "0.1.0",
            "installed_version": "0.1.0",
            "message": sample_index["rules"][0]["message"],
            "learn_more": sample_index["rules"][0]["source"],
        }
    ]
    assert report["index_generated_utc"] == "2026-08-08T09:00:00Z"


def test_clean_and_unknown_domains_are_separated(sample_index):
    report = build_report(
        sample_index,
        {
            "fixture_tracker": "0.1.0",
            "lookalike_tracker": "2.0.0",
            "something_local": "9.9.9",
            "breakage_radar": "1.0.0",
        },
    )
    assert report["affected_count"] == 1
    assert report["clean_domains"] == ["lookalike_tracker"]
    assert report["not_in_index"] == ["something_local"]
    # Breakage Radar never reports on itself.
    assert report["installed_count"] == 3


def test_empty_installation_gives_an_empty_report(sample_index):
    report = build_report(sample_index, {})
    assert report["affected_count"] == 0
    assert report["by_release"] == {}
    assert report["earliest_release"] is None


def test_details_are_capped(sample_index):
    integration = sample_index["integrations"][0]
    finding = integration["findings"][0]
    integration["findings"] = [
        {**finding, "line": n} for n in range(1, MAX_DETAILS + 25)
    ]
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    assert len(report["details"]) == MAX_DETAILS
    assert report["details_truncated"] is True
    assert report["total_findings"] == MAX_DETAILS + 24


def test_garbage_index_produces_an_empty_report_not_an_exception():
    assert build_report({}, {"a": "1"})["affected_count"] == 0
    assert build_report({"integrations": "nope"}, {"a": "1"})["affected_count"] == 0


# --------------------------------------------------------------------------- #
# validate_index
# --------------------------------------------------------------------------- #


def test_validate_index_accepts_the_real_shape(sample_index):
    assert validate_index(sample_index) is None


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ("not a dict", "expected an object"),
        ({"schema": 99, "rules": [], "integrations": []}, "not supported"),
        ({"schema": 1, "rules": []}, "integrations"),
        ({"schema": 1, "integrations": []}, "rules"),
    ],
)
def test_validate_index_rejects_bad_payloads(payload, fragment):
    problem = validate_index(payload)
    assert problem and fragment in problem


# --------------------------------------------------------------------------- #
# sensor
# --------------------------------------------------------------------------- #


def test_sensor_state_and_attributes(sample_index):
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    sensor = BreakageRadarSensor(FakeCoordinator(report))

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes[ATTR_BY_RELEASE] == {
        "2027.5": ["fixture_tracker"]
    }
    assert sensor.entity_id == "sensor.breakage_radar_affected"
    assert sensor.extra_state_attributes["details"][0]["line"] == 12
    assert sensor.extra_state_attributes["index_generated_utc"] == "2026-08-08T09:00:00Z"
    assert "last_error" not in sensor.extra_state_attributes


def test_sensor_is_unavailable_and_keeps_the_last_report_on_failure(sample_index):
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    coordinator = FakeCoordinator(report, success=False, error="HTTP 503")
    sensor = BreakageRadarSensor(coordinator)

    assert sensor.available is False
    # The last good numbers are still there for anyone who looks.
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["last_error"] == "HTTP 503"


def test_sensor_with_no_data_yet_has_no_state():
    sensor = BreakageRadarSensor(FakeCoordinator(None))
    assert sensor.native_value is None
    assert sensor.extra_state_attributes[ATTR_BY_RELEASE] == {}


# --------------------------------------------------------------------------- #
# repairs
# --------------------------------------------------------------------------- #


def test_repairs_issue_is_raised_and_cleared(sample_index):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.breakage_radar.const import DOMAIN, ISSUE_ID
    from custom_components.breakage_radar.repairs import async_sync_issue

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own test harness")

    ir.created.clear()
    affected = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    async_sync_issue(None, affected)
    assert (DOMAIN, ISSUE_ID) in ir.created
    placeholders = ir.created[(DOMAIN, ISSUE_ID)]["translation_placeholders"]
    assert placeholders["count"] == "1"
    assert placeholders["earliest"] == "2027.5"
    assert ir.created[(DOMAIN, ISSUE_ID)]["is_fixable"] is False

    async_sync_issue(None, build_report(sample_index, {}))
    assert (DOMAIN, ISSUE_ID) not in ir.created


def test_broken_now_gets_its_own_error_issue_and_clears_on_recovery(sample_index):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.breakage_radar.const import DOMAIN, ISSUE_ID
    from custom_components.breakage_radar.repairs import async_sync_issue

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own test harness")

    ir.created.clear()
    # Running the release that removed the API: the finding is broken_now.
    report = build_report(
        sample_index, {"fixture_tracker": "0.1.0"}, current_version="2027.5"
    )
    assert report["broken_now"] == {"fixture_tracker": "2027.5"}
    async_sync_issue(None, report)

    broken_key = (DOMAIN, "broken_now_fixture_tracker")
    assert broken_key in ir.created
    assert ir.created[broken_key]["severity"] == ir.IssueSeverity.ERROR
    assert ir.created[broken_key]["translation_placeholders"] == {
        "domain": "fixture_tracker",
        "release": "2027.5",
    }
    # The aggregate escalates too while something is broken right now.
    assert ir.created[(DOMAIN, ISSUE_ID)]["severity"] == ir.IssueSeverity.ERROR

    # The integration ships a fix (its findings disappear): both issues clear.
    fixed = json.loads(json.dumps(sample_index))
    fixed["integrations"] = []
    fixed["clean_domains"] = ["fixture_tracker"]
    async_sync_issue(
        None, build_report(fixed, {"fixture_tracker": "0.2.0"}, current_version="2027.5")
    )
    assert broken_key not in ir.created
    assert (DOMAIN, ISSUE_ID) not in ir.created


def test_broken_now_issue_for_an_uninstalled_component_is_swept(sample_index):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.breakage_radar.repairs import async_sync_issue

    if not hasattr(ir, "created"):
        pytest.skip("real Home Assistant installed; covered by HA's own test harness")

    ir.created.clear()
    report = build_report(
        sample_index, {"fixture_tracker": "0.1.0"}, current_version="2027.5"
    )
    async_sync_issue(None, report)
    assert (
        "breakage_radar",
        "broken_now_fixture_tracker",
    ) in ir.created

    # The user uninstalls it: the domain vanishes from the report entirely,
    # so only the registry sweep can find the stale issue.
    async_sync_issue(None, build_report(sample_index, {}, current_version="2027.5"))
    assert (
        "breakage_radar",
        "broken_now_fixture_tracker",
    ) not in ir.created


# --------------------------------------------------------------------------- #
# manifest / packaging
# --------------------------------------------------------------------------- #


def test_manifest_is_hacs_and_hassfest_shaped(repo_root):
    manifest = json.loads(
        (repo_root / "custom_components" / "breakage_radar" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for key in (
        "domain",
        "name",
        "codeowners",
        "config_flow",
        "documentation",
        "iot_class",
        "issue_tracker",
        "requirements",
        "version",
    ):
        assert key in manifest, f"manifest.json is missing {key!r}"
    assert manifest["domain"] == "breakage_radar"
    assert manifest["requirements"] == [], "the integration must have no runtime deps"
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert manifest["version"] == declared, "manifest and pyproject versions must agree"


def test_translations_match_strings(repo_root):
    base = repo_root / "custom_components" / "breakage_radar"
    strings = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (base / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == english
    assert "integrations_affected" in strings["issues"]
