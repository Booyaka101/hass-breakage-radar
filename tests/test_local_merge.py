"""Local verdict plus index verdict: which one wins, and what survives.

The merge is what gives forked, renamed and non-HACS integrations a real
verdict instead of ``not_in_index``. The scan that feeds it lives in
``test_local_scan.py``.

Merge order: local affected > local clean > index affected > index clean >
local unknown (with a reason) > unknown.
"""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import FakeCoordinator, install_component as _install, scan_components as _scan
from custom_components.breakage_radar.report import build_report
from custom_components.breakage_radar.scanner import scan_installed
from custom_components.breakage_radar.sensor import BreakageRadarSensor


# --------------------------------------------------------------------------- #
# the worked example: a domain the index has never heard of
# --------------------------------------------------------------------------- #


def test_true_positive_not_in_index_gets_a_local_finding(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"fixture_tracker": "0.1.0"}, local
    )
    sensor = BreakageRadarSensor(FakeCoordinator(report))

    assert sensor.native_value == 1
    assert [g["release"] for g in report["schedule"]] == ["2027.5"]
    assert report["details"] == [
        {
            "domain": "fixture_tracker",
            "rule_id": "legacy-device-tracker-platform",
            "breaks_in": "2027.5",
            "file": "custom_components/fixture_tracker/device_tracker.py",
            "line": 12,
            "confidence": "high",
            "source": "local",
            "when": "upcoming",
            "days_until": None,
            "due": "May 2027",
            "repository": "",
            "scanned_version": "0.1.0",
            "installed_version": "0.1.0",
            "message": index_without_fixture_tracker["rules"][0]["message"],
            "learn_more": index_without_fixture_tracker["rules"][0]["source"],
        }
    ]
    assert "fixture_tracker" not in report["not_in_index"]
    assert report["files_scanned"] == 1
    assert report["unparsed_files"] == 0
    assert report["skipped_files"] == 0


def test_false_positive_not_in_index_parses_clean(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    components = _install(
        tmp_path, fixtures_dir, "false_positive", "lookalike_tracker"
    )
    local = _scan(components, index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"lookalike_tracker": "0.1.0"}, local
    )

    assert report["affected_count"] == 0
    assert report["details"] == []
    assert report["clean_domains"] == ["lookalike_tracker"]
    assert report["not_in_index"] == []


# --------------------------------------------------------------------------- #
# local verdicts replace index verdicts
# --------------------------------------------------------------------------- #


def test_local_findings_replace_index_findings_for_the_same_domain(
    tmp_path, fixtures_dir, sample_index
):
    """The index scanned an older release and recorded the finding at the
    wrong line; the local scan of the installed bytes wins."""
    sample_index["integrations"][0]["findings"][0]["line"] = 99
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, sample_index)
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"}, local)

    assert report["affected_count"] == 1
    assert len(report["details"]) == 1
    detail = report["details"][0]
    assert detail["line"] == 12
    assert detail["source"] == "local"
    # The index entry still contributes what only it can know.
    assert detail["repository"] == "example/fixture-tracker"
    assert detail["scanned_version"] == detail["installed_version"] == "0.1.0"


def test_local_clean_overrides_a_stale_index_finding(
    tmp_path, fixtures_dir, sample_index
):
    """The user updated to a fixed release the crawler has not revisited yet:
    the installed code no longer contains the finding, so the domain is clean."""
    components = _install(
        tmp_path, fixtures_dir, "false_positive", "lookalike_tracker"
    )
    # Point the index's affected entry at the domain that is now clean on disk.
    sample_index["integrations"][0]["domain"] = "lookalike_tracker"
    sample_index["integrations"][0]["domains"] = ["lookalike_tracker"]
    local = _scan(components, sample_index)
    report = build_report(sample_index, {"lookalike_tracker": "2.0.0"}, local)

    assert report["affected_count"] == 0
    assert report["clean_domains"] == ["lookalike_tracker"]


def test_without_a_local_scan_the_index_verdict_still_stands(sample_index):
    """index-only matching (the pre-1.1.0 behaviour) is unchanged."""
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    assert report["affected_count"] == 1
    assert report["details"][0]["source"] == "index"
    assert report["local_scan_enabled"] is False


# --------------------------------------------------------------------------- #
# v1.1.1 regressions: a fork keeps its verdict whatever the directory is named
# --------------------------------------------------------------------------- #


def test_renamed_directory_is_keyed_by_its_manifest_domain(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    """v1.1.0 keyed the scan by directory name but the merge looked up the
    manifest domain, so a fork's local finding was silently dropped."""
    components = tmp_path / "custom_components"
    source = (
        fixtures_dir / "true_positive" / "custom_components" / "fixture_tracker"
    )
    shutil.copytree(source, components / "my_fork_of_tracker")
    (components / "my_fork_of_tracker" / "manifest.json").write_text(
        json.dumps({"domain": "fixture_tracker", "version": "9.9"}),
        encoding="utf-8",
    )

    installed = {"fixture_tracker": "9.9"}
    local = _scan(components, index_without_fixture_tracker)
    report = build_report(index_without_fixture_tracker, installed, local)

    assert list(local["domains"]) == ["fixture_tracker"]
    assert report["affected_domains"] == ["fixture_tracker"]
    assert report["not_in_index"] == []
    # The finding path shows where the code actually lives on disk.
    assert report["details"][0]["file"] == (
        "custom_components/my_fork_of_tracker/device_tracker.py"
    )


def test_our_own_shipped_component_is_clean(repo_root, sample_index):
    """Dogfood: run the shipped rules over the shipped integration.

    This is the guard the old self-exclusion was hiding. If Breakage Radar ever
    starts using an API Home Assistant is removing, this fails in CI -- which
    is exactly what it would tell any other integration author to want.
    """
    local = scan_installed(
        str(repo_root / "custom_components"),
        sample_index["rules"],
        current_version=sample_index["core_version"],
    )
    ours = local["domains"]["breakage_radar"]
    assert ours["findings"] == []
    assert ours["status"] == "clean", ours["reason"]
    assert ours["unparsed_files"] == 0


# --------------------------------------------------------------------------- #
# v1.1.1 regressions: a scan with no rules proves nothing
# --------------------------------------------------------------------------- #


def test_an_index_with_no_matchers_cannot_launder_domains_clean(
    tmp_path, fixtures_dir, sample_index
):
    """v1.1.0 let a zero-rule scan mark every domain clean, overriding real
    index findings."""
    for rule in sample_index["rules"]:
        rule.pop("match", None)
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, sample_index)
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"}, local)

    assert local["rules_matchable"] == 0
    assert local["domains"]["fixture_tracker"]["status"] == "unknown"
    # The index finding survives, attributed to the index.
    assert report["affected_domains"] == ["fixture_tracker"]
    assert report["details"][0]["source"] == "index"
    assert report["clean_domains"] == []


def test_no_matchers_and_no_index_entry_is_unknown_with_a_reason(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    for rule in index_without_fixture_tracker["rules"]:
        rule.pop("match", None)
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"fixture_tracker": "0.1.0"}, local
    )

    assert report["not_in_index"] == ["fixture_tracker"]
    assert "no matchable rules" in report["not_in_index_reasons"]["fixture_tracker"]


# --------------------------------------------------------------------------- #
# v1.1.1 regressions: a passed deadline must get MORE visible, never less
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "running,expected_when",
    [("2026.9", "upcoming"), ("2027.4", "upcoming"), ("2027.5", "broken_now"),
     ("2027.9", "broken_now")],
)
def test_passed_deadlines_stay_visible_and_escalate(
    tmp_path, fixtures_dir, sample_index, running, expected_when
):
    """v1.1.0 dropped past-deadline rules from the local scan, so upgrading
    *onto* the breaking release flipped an affected domain to clean."""
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, sample_index)
    report = build_report(
        sample_index,
        {"fixture_tracker": "0.1.0"},
        local,
        current_version=running,
    )

    assert report["affected_domains"] == ["fixture_tracker"]
    assert report["clean_domains"] == []
    assert report["details"][0]["when"] == expected_when
    if expected_when == "broken_now":
        assert report["broken_now"] == {"fixture_tracker": "2027.5"}
        assert report["broken_now_count"] == 1
    else:
        assert report["broken_now"] == {}


def test_without_a_version_everything_is_conservatively_upcoming(sample_index):
    report = build_report(sample_index, {"fixture_tracker": "0.1.0"})
    assert report["details"][0]["when"] == "upcoming"
    assert report["broken_now"] == {}
