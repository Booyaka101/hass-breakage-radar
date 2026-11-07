"""An offline upgrade plan must not turn missing evidence into a clean bill."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from tools.upgrade_report import build_upgrade_report, main, release_key, render_text


@pytest.fixture
def diagnostics():
    return {
        "index": {"generated_utc": "2026-11-07T00:00:00Z", "schema": 1},
        "settings": {"ignored_domains": []},
        "scan": {
            "enabled": True, "unparsed_files": 0, "skipped_files": 0,
            "skipped_minified_files": 0,
        },
        "summary": {"not_analysed": []},
        "cards": {"not_analysed": []},
        "findings": [], "findings_truncated": False, "total_findings": 0,
    }


def finding(release, **kwargs):
    return {
        "domain": "example", "kind": "integration", "rule_id": "removed_api",
        "breaks_in": release, "source": "local", "file": "sensor.py", "line": 10,
        "installed_version": "1.0", "scanned_version": "1.0",
        "message": "Update the deprecated API", "learn_more": "https://example.org/removal",
        **kwargs,
    }


def populate(data, findings):
    data["findings"] = findings
    data["total_findings"] = len(findings)
    return data


def test_partitions_inclusive_target_without_dropping_existing_breakage(diagnostics):
    populate(diagnostics, [finding(v) for v in ("2026.1", "2026.12", "2027.1", "")])
    report = build_upgrade_report({"data": diagnostics}, "2026.12.1")
    assert [f["breaks_in"] for f in report["due_by_target"]] == ["2026.1", "2026.12"]
    assert len(report["after_target"]) == len(report["unknown_deadline"]) == 1
    assert report["status"] == "known_findings"
    assert report["has_coverage_gaps"]
    text = render_text(report)
    assert "Update the deprecated API" in text
    assert "https://example.org/removal" in text
    assert "not a guarantee" in text


def test_no_findings_is_explicitly_not_compatibility(diagnostics):
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "no_known_findings"
    assert not report["has_coverage_gaps"]
    assert "not a guarantee of compatibility" in render_text(report)
    assert "Fixed integration releases are not established" in render_text(report)


@pytest.mark.parametrize("version", ["2026.0", "2026.13", "2026.12b1", "dev", "", "2026.12.0junk", None])
def test_invalid_target_rejected(version):
    with pytest.raises(ValueError):
        release_key(version)


@pytest.mark.parametrize("section,key,value", [
    ("scan", "enabled", False),
    ("scan", "unparsed_files", 1),
    ("scan", "skipped_files", 2),
    ("scan", "skipped_minified_files", None),
    ("settings", "ignored_domains", ["ignored"]),
    ("summary", "not_analysed", ["unreadable"]),
    ("cards", "not_analysed", ["unknown-card"]),
])
def test_gaps_never_read_as_clean(diagnostics, section, key, value):
    diagnostics[section][key] = value
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert report["coverage_gaps"]


def test_index_evidence_is_preserved_but_not_claimed_as_installed(diagnostics):
    populate(diagnostics, [finding("2026.12", source="index", scanned_version="2.0")])
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["due_by_target"][0]["installed_version"] == "1.0"
    assert report["due_by_target"][0]["scanned_version"] == "2.0"
    assert any("crawled code" in gap for gap in report["coverage_gaps"])


def test_truncated_export_counts_are_lower_bounds(diagnostics):
    diagnostics["total_findings"] = 100
    diagnostics["findings_truncated"] = True
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert "lower bounds" in render_text(report)


def test_patch_boundaries_and_card_evidence(diagnostics):
    populate(diagnostics, [finding("2026.12.1", kind="card"), finding("2026.12.2")])
    report = build_upgrade_report(diagnostics, "2026.12.1")
    assert report["due_by_target"][0]["kind"] == "card"
    assert report["after_target"][0]["breaks_in"] == "2026.12.2"


def test_missing_index_date_is_a_gap(diagnostics):
    diagnostics["index"].pop("generated_utc")
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert "freshness cannot be checked" in render_text(report)


def test_previous_export_truncation_is_disclosed_without_mutation(diagnostics):
    old = copy.deepcopy(diagnostics)
    old["findings_truncated"] = True
    old["total_findings"] = 100
    before = copy.deepcopy(diagnostics)
    report = build_upgrade_report(diagnostics, "2026.12", previous=old)
    assert report["changes"]["exports_truncated"]
    assert "Comparison incomplete" in render_text(report)
    assert diagnostics == before


def test_comparison_ignores_line_moves_and_never_claims_fixes(diagnostics):
    old = populate(copy.deepcopy(diagnostics), [finding("2026.12"), finding("2026.11")])
    populate(diagnostics, [finding("2026.12", line=50), finding("2027.1", kind="card")])
    changes = build_upgrade_report(diagnostics, "2026.12", previous=old)["changes"]
    assert [f["breaks_in"] for f in changes["newly_observed"]] == ["2027.1"]
    assert [f["breaks_in"] for f in changes["no_longer_observed"]] == ["2026.11"]
    assert "not confirmed fixes" in changes["note"]


@pytest.mark.parametrize("payload", [{}, [], {"data": {}}, {"findings": []}])
def test_unrelated_json_is_not_a_clean_report(payload):
    with pytest.raises(ValueError):
        build_upgrade_report(payload, "2026.12")


@pytest.mark.parametrize("condition,expected", [("clean", 0), ("due", 1), ("gap", 2), ("both", 2)])
def test_cli_exit_contract(tmp_path, diagnostics, capsys, condition, expected):
    if condition in ("due", "both"):
        populate(diagnostics, [finding("2026.12")])
    if condition in ("gap", "both"):
        diagnostics["scan"]["skipped_files"] = 1
    path = tmp_path / "diagnostics.json"
    path.write_text(json.dumps({"data": diagnostics}), encoding="utf-8-sig")
    assert main([str(path), "--target-version", "2026.12", "--format", "json"]) == expected
    assert json.loads(capsys.readouterr().out)["target_version"] == "2026.12"


def test_cli_invalid_json_and_missing_file(tmp_path, capsys):
    path = tmp_path / "bad.json"
    assert main([str(path), "--target-version", "2026.12"]) == 2
    path.write_text("not JSON", encoding="utf-8")
    assert main([str(path), "--target-version", "2026.12"]) == 2
    assert "Could not build upgrade report" in capsys.readouterr().err


def test_consumes_actual_integration_diagnostics():
    from custom_components.breakage_radar.const import DOMAIN
    from custom_components.breakage_radar.diagnostics import async_get_config_entry_diagnostics
    from custom_components.breakage_radar.report import build_report

    index = {
        "rules": [{"id": "removed_api", "breaks_in": "2026.12"}],
        "generated_utc": "2026-11-07T00:00:00Z",
    }
    local = {
        "domains": {"example": {"status": "affected", "findings": [finding("2026.12")]}},
        "rules_matchable": 1,
    }
    coordinator = SimpleNamespace(
        data=build_report(index, {"example": "1.0"}, local),
        index_url="https://example.org/index.json", alert_window_days=30,
    )
    hass = SimpleNamespace(data={DOMAIN: {"test": coordinator}})
    export = asyncio.run(async_get_config_entry_diagnostics(hass, SimpleNamespace(entry_id="test")))
    report = build_upgrade_report({"data": export}, "2026.12")
    assert report["status"] == "known_findings"
    assert report["due_by_target"][0]["installed_version"] == "1.0"
    assert not report["has_coverage_gaps"]
