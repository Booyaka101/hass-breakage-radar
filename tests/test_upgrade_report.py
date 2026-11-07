"""An offline upgrade plan must not turn missing evidence into a clean bill."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tools.upgrade_report import build_upgrade_report, main, release_key, render_text


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("tools.upgrade_report._utcnow", lambda: datetime(2026, 11, 7, 12, tzinfo=UTC))


@pytest.fixture
def diagnostics():
    return {
        "index": {"generated_utc": "2026-11-07T00:00:00Z", "schema": 1},
        "settings": {"ignored_domains": []},
        "scan": {
            "enabled": True, "unparsed_files": 0, "skipped_files": 0,
            "skipped_minified_files": 0,
            "card_unparsed_files": 0, "card_skipped_files": 0,
        },
        "summary": {"installed": 1, "installed_versions": {"example": "1.0"}, "not_analysed": []},
        "cards": {"installed": 0, "not_analysed": []},
        "health": {"index_update_success": True, "index_error": None,
                   "scan_errors": {"integrations": None, "cards": None}},
        "coverage": {
            "integrations": {
                "rules_matchable": 1,
                "completed_utc": "2026-11-07T00:00:00Z",
                "index_generated_utc": "2026-11-07T00:00:00Z",
                "discovered_versions": {"example": "1.0"},
                "components": {"example": {
                    "status": "clean", "files_scanned": 1, "unparsed_files": 0,
                    "skipped_files": 0, "unreadable_directories": 0,
                }},
            },
            "cards": {"rules_matchable": 1, "components": {},
                      "completed_utc": "2026-11-07T00:00:00Z",
                      "index_generated_utc": "2026-11-07T00:00:00Z"},
        },
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
    for f in findings:
        if f.get("kind") == "card":
            data["cards"]["installed"] = 1
            data["coverage"]["cards"]["components"][f["domain"]] = {
                "status": "affected", "files_scanned": 1,
                "unparsed_files": 0, "skipped_files": 0, "skipped_minified": 0,
                "unreadable_directories": 0,
            }
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
    if section == "cards" and key == "not_analysed":
        diagnostics["cards"]["installed"] = len(value)
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
        "completed_utc": "2026-11-07T00:00:00Z",
        "index_generated_utc": "2026-11-07T00:00:00Z",
        "discovered_versions": {"example": "1.0"},
        "domains": {"example": {
            "status": "affected", "findings": [finding("2026.12")], "files_scanned": 1,
            "unparsed_files": 0, "skipped_files": 0, "unreadable_directories": 0,
        }},
        "rules_matchable": 1,
    }
    coordinator = SimpleNamespace(
        data=build_report(index, {"example": "1.0"}, local),
        index_url="https://example.org/index.json", alert_window_days=30,
        last_update_success=True, last_error=None,
        scan_errors={"integrations": None, "cards": None},
    )
    hass = SimpleNamespace(data={DOMAIN: {"test": coordinator}})
    export = asyncio.run(async_get_config_entry_diagnostics(hass, SimpleNamespace(entry_id="test")))
    report = build_upgrade_report({"data": export}, "2026.12")
    assert report["status"] == "known_findings"
    assert report["due_by_target"][0]["installed_version"] == "1.0"
    assert not report["has_coverage_gaps"]


@pytest.mark.parametrize("generated", [None, 42, "not-a-date", "2026-11-07", "2026-11-07T00:00:00"])
def test_invalid_or_timezone_free_index_timestamp_is_a_gap(diagnostics, generated):
    diagnostics["index"]["generated_utc"] = generated
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert "freshness cannot be checked" in render_text(report)


@pytest.mark.parametrize("generated,age,has_gap", [
    ("2026-10-31T12:00:00Z", 7, False),
    ("2026-10-31T11:59:59Z", 7, True),
    ("2026-10-30T12:00:00Z", 10, False),
    ("2026-11-07T12:04:00Z", 7, False),
    ("2026-11-07T12:06:00Z", 7, True),
    ("2026-11-07T07:00:00-05:00", 0, False),
])
def test_freshness_boundaries(diagnostics, generated, age, has_gap):
    diagnostics["index"]["generated_utc"] = generated
    diagnostics["coverage"]["integrations"]["index_generated_utc"] = generated
    diagnostics["coverage"]["integrations"]["completed_utc"] = "2026-11-07T12:00:00Z"
    assert build_upgrade_report(
        diagnostics, "2026.12", max_index_age_days=age,
    )["has_coverage_gaps"] is has_gap


@pytest.mark.parametrize("age", [-1, True, 1.5, "7"])
def test_invalid_freshness_limit_is_rejected(diagnostics, age):
    with pytest.raises(ValueError, match="nonnegative integer"):
        build_upgrade_report(diagnostics, "2026.12", max_index_age_days=age)


@pytest.mark.parametrize("change", ["missing", "unknown", "no_rules", "no_files", "partial"])
def test_clean_index_cannot_mask_missing_local_coverage(diagnostics, change):
    coverage = diagnostics["coverage"]["integrations"]
    if change == "missing":
        diagnostics.pop("coverage")
    elif change == "unknown":
        coverage["components"]["example"]["status"] = "unknown"
    elif change == "no_rules":
        coverage["rules_matchable"] = 0
    elif change == "no_files":
        coverage["components"]["example"]["files_scanned"] = 0
    else:
        coverage["components"]["example"]["unreadable_directories"] = 1
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert report["has_coverage_gaps"]


def test_comparison_detects_removed_duplicate_match(diagnostics):
    old = populate(copy.deepcopy(diagnostics), [finding("2026.12", line=1), finding("2026.12", line=10)])
    populate(diagnostics, [finding("2026.12", line=12)])
    changes = build_upgrade_report(diagnostics, "2026.12", previous=old)["changes"]
    assert not changes["newly_observed"]
    assert len(changes["no_longer_observed"]) == 1
    # The precise removed location is not established when line numbers move.
    assert "not confirmed fixes" in changes["note"]


def test_comparison_normalizes_equivalent_deadlines_and_default_kind(diagnostics):
    old = populate(copy.deepcopy(diagnostics), [finding("2026.12")])
    old["findings"][0].pop("kind")
    populate(diagnostics, [finding("2026.12.0")])
    changes = build_upgrade_report(diagnostics, "2026.12", previous=old)["changes"]
    assert not changes["newly_observed"]
    assert not changes["no_longer_observed"]


def test_previous_gaps_make_comparison_incomplete(diagnostics):
    old = copy.deepcopy(diagnostics)
    old.pop("coverage")
    report = build_upgrade_report(diagnostics, "2026.12", previous=old)
    assert report["status"] == "incomplete"
    assert report["has_coverage_gaps"]
    assert report["changes"]["previous_coverage_gaps"]
    assert "Previous export:" in render_text(report)


@pytest.mark.parametrize("bad", ["null", "[]", '{"findings": [], "findings": []}', '{"value": NaN}'])
def test_cli_rejects_invalid_previous_export(tmp_path, diagnostics, capsys, bad):
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    current.write_text(json.dumps(diagnostics), encoding="utf-8")
    previous.write_text(bad, encoding="utf-8")
    assert main([str(current), "--target-version", "2026.12", "--previous", str(previous)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Could not build upgrade report" in captured.err


def test_text_output_escapes_terminal_controls_and_non_ascii(diagnostics):
    populate(diagnostics, [finding("2026.12", message="\x1b[2J\rFAKE\nVERDICT\t\u2603")])
    text = render_text(build_upgrade_report(diagnostics, "2026.12"))
    assert "\x1b" not in text and "\r" not in text and "\t" not in text
    assert "\\u001b[2J\\rFAKE\\nVERDICT\\t\\u2603" in text
    text.encode("ascii")
    assert "\n\nDue by target: 1\n" in text


@pytest.mark.parametrize("section,key,value", [
    ("summary", "installed", True), ("cards", "installed", -1),
    ("settings", "ignored_domains", [None]),
])
def test_invalid_diagnostics_metadata_rejected(diagnostics, section, key, value):
    diagnostics[section][key] = value
    with pytest.raises(ValueError):
        build_upgrade_report(diagnostics, "2026.12")


def test_local_scan_preserves_unreadable_directory_count_even_with_findings(tmp_path):
    from custom_components.breakage_radar.rules_engine import Rule
    from custom_components.breakage_radar.scanner import _scan_domain

    source = tmp_path / "sensor.py"
    source.write_text("old_api()\n", encoding="utf-8")
    rule = Rule("removed_api", "call", "old_api", "Update API", "2026.12", "",
                match={"type": "call", "names": ["old_api"]})
    result = _scan_domain("example", [(str(source), "sensor.py")], 1, [rule],
                          max_files=100, max_bytes=10000)
    assert result["status"] == "affected"
    assert result["findings"]
    assert result["unreadable_directories"] == 1


@pytest.mark.parametrize("card_problem", ["unparsed_files", "skipped_files", "skipped_minified"])
def test_actual_diagnostics_preserve_card_scan_gaps(card_problem):
    from custom_components.breakage_radar.const import DOMAIN
    from custom_components.breakage_radar.diagnostics import async_get_config_entry_diagnostics
    from custom_components.breakage_radar.report import build_report
    from custom_components.breakage_radar.sensor import BreakageRadarSensor

    card = {
        "status": "clean", "files_scanned": 1, "unparsed_files": 0,
        "skipped_files": 0, "skipped_minified": 0, "unreadable_directories": 0,
    }
    card[card_problem] = 1
    scan = {
        "cards": {"example-card": card}, "rules_matchable": 1, card_problem: 1,
        "completed_utc": "2026-11-07T00:00:00Z",
        "index_generated_utc": "2026-11-07T00:00:00Z",
    }
    report = build_report(
        {"generated_utc": "2026-11-07T00:00:00Z"}, {}, cards=["example-card"], local_card_scan=scan,
    )
    coordinator = SimpleNamespace(
        data=report, index_url="https://example.org/index.json", alert_window_days=30,
        last_error=None,
        last_update_success=True, scan_errors={"integrations": None, "cards": None},
    )
    hass = SimpleNamespace(data={DOMAIN: {"test": coordinator}})
    export = asyncio.run(async_get_config_entry_diagnostics(hass, SimpleNamespace(entry_id="test")))
    upgrade = build_upgrade_report(export, "2026.12")
    assert upgrade["status"] == "incomplete"
    assert any(card_problem in gap for gap in upgrade["coverage_gaps"])
    # Detailed coverage stays in diagnostics, never in recorder state attributes.
    assert "scan_coverage" not in BreakageRadarSensor(coordinator).extra_state_attributes


def test_cli_previous_truncation_returns_two(tmp_path, diagnostics, capsys):
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    current.write_text(json.dumps(diagnostics), encoding="utf-8")
    diagnostics["findings_truncated"] = True
    diagnostics["total_findings"] = 10
    previous.write_text(json.dumps(diagnostics), encoding="utf-8")
    assert main([str(current), "--target-version", "2026.12", "--previous", str(previous)]) == 2
    assert "comparison is incomplete" in capsys.readouterr().out


def test_previous_unknown_deadline_marks_comparison_incomplete(diagnostics):
    old = populate(copy.deepcopy(diagnostics), [finding("undated")])
    report = build_upgrade_report(diagnostics, "2026.12", previous=old)
    assert report["status"] == "incomplete"
    assert any("no supported release deadline" in gap for gap in report["changes"]["previous_coverage_gaps"])


def test_large_freshness_limit_does_not_overflow(diagnostics):
    report = build_upgrade_report(diagnostics, "2026.12", max_index_age_days=10**100)
    assert report["status"] == "no_known_findings"


def test_cli_deep_json_has_a_controlled_error(tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    assert main([str(path), "--target-version", "2026.12"]) == 2
    assert "Could not build upgrade report" in capsys.readouterr().err


def test_card_scan_preserves_unreadable_directory_count():
    from custom_components.breakage_radar.scanner import _scan_card

    result = _scan_card("card", [], 2, [], max_files=100, max_bytes=10000)
    assert result["unreadable_directories"] == 2


def test_cli_age_override_and_valid_previous_are_offline(tmp_path, diagnostics, capsys, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        pytest.fail("offline report attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    diagnostics["index"]["generated_utc"] = "2026-10-30T12:00:00Z"
    diagnostics["coverage"]["integrations"]["index_generated_utc"] = diagnostics["index"]["generated_utc"]
    path = tmp_path / "current.json"
    path.write_text(json.dumps(diagnostics), encoding="utf-8")
    assert main([
        str(path), "--target-version", "2026.12", "--previous", str(path),
        "--max-index-age-days", "10", "--format", "json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["max_index_age_days"] == 10
    assert not report["changes"]["newly_observed"]
    assert not report["changes"]["no_longer_observed"]


@pytest.mark.parametrize("change", ["timestamp", "index", "version", "identity", "health", "pending"])
def test_snapshot_provenance_gaps_are_not_clean(diagnostics, change):
    group = diagnostics["coverage"]["integrations"]
    if change == "timestamp":
        group["completed_utc"] = "2026-11-05T00:00:00Z"
    elif change == "index":
        group["index_generated_utc"] = "2026-11-06T00:00:00Z"
    elif change == "version":
        group["discovered_versions"]["example"] = "0.9"
    elif change == "identity":
        group["components"]["other"] = group["components"].pop("example")
    elif change == "health":
        diagnostics["health"]["scan_errors"]["integrations"] = "scan failed"
    else:
        diagnostics["health"]["local_scan_in_progress"] = True
    report = build_upgrade_report(diagnostics, "2026.12")
    assert report["status"] == "incomplete"
    assert report["has_coverage_gaps"]


def test_historical_baseline_does_not_need_to_be_fresh_today(diagnostics):
    old = copy.deepcopy(diagnostics)
    old["index"]["generated_utc"] = "2025-01-01T00:00:00Z"
    group = old["coverage"]["integrations"]
    group["index_generated_utc"] = "2025-01-01T00:00:00Z"
    group["completed_utc"] = "2025-01-01T12:00:00Z"
    report = build_upgrade_report(diagnostics, "2026.12", previous=old)
    assert report["changes"]["comparison_complete"]
    assert report["changes"]["previous_index"] == old["index"]
    assert report["status"] == "no_known_findings"


def test_duplicate_comparison_retains_exact_unchanged_location(diagnostics):
    old = populate(copy.deepcopy(diagnostics), [finding("2026.12", line=1), finding("2026.12", line=10)])
    populate(diagnostics, [finding("2026.12", line=10)])
    changes = build_upgrade_report(diagnostics, "2026.12", previous=old)["changes"]
    assert [f["line"] for f in changes["no_longer_observed"]] == [1]
    assert not changes["newly_observed"]


@pytest.mark.parametrize("metadata,value", [("index_update_success", False), ("index_error", "refresh failed")])
def test_failed_index_health_is_not_overridden_by_recent_scan(diagnostics, metadata, value):
    diagnostics["health"][metadata] = value
    assert build_upgrade_report(diagnostics, "2026.12")["status"] == "incomplete"


@pytest.mark.parametrize("age,has_gap", [(0, True), (1, False), (10**100, False)])
def test_scan_age_limit_is_independent(diagnostics, age, has_gap):
    assert build_upgrade_report(diagnostics, "2026.12", max_scan_age_days=age)["has_coverage_gaps"] is has_gap


@pytest.mark.parametrize("age", [-1, True, 1.5])
def test_invalid_scan_age_limit(diagnostics, age):
    with pytest.raises(ValueError, match="max_scan_age_days"):
        build_upgrade_report(diagnostics, "2026.12", max_scan_age_days=age)


@pytest.mark.parametrize("section,key,value", [
    ("summary", "affected", 1), ("summary", "affected", -1),
    ("summary", "installed_versions", {"example": 1}),
    ("summary", "not_analysed", ["a", "b"]),
    ("cards", "affected", ["card"]),
])
def test_inconsistent_metadata_does_not_claim_clean(diagnostics, section, key, value):
    diagnostics[section][key] = value
    with pytest.raises(ValueError):
        build_upgrade_report(diagnostics, "2026.12")


@pytest.mark.parametrize("line", [True, -1, 1.5, "10"])
def test_malformed_finding_line_is_rejected(diagnostics, line):
    populate(diagnostics, [finding("2026.12", line=line)])
    with pytest.raises(ValueError, match="finding line"):
        build_upgrade_report(diagnostics, "2026.12")


def test_ignore_filters_scan_time_versions_from_diagnostics():
    from custom_components.breakage_radar.report import build_report

    local = {"domains": {}, "discovered_versions": {"private_ignored": "1.0"}}
    report = build_report({}, {"private_ignored": "1.0"}, local, ignored_domains=["private_ignored"])
    assert report["installed_versions"] == {}
    assert report["scan_coverage"]["integrations"]["discovered_versions"] == {}


def test_scan_chronology_and_extreme_dates_do_not_overflow(diagnostics):
    group = diagnostics["coverage"]["integrations"]
    group["completed_utc"] = "2026-11-06T23:00:00Z"
    report = build_upgrade_report(diagnostics, "2026.12")
    assert any("chronology is inconsistent" in gap for gap in report["coverage_gaps"])
    for key in ("completed_utc", "index_generated_utc"):
        group[key] = "0001-01-01T00:00:00Z"
    diagnostics["index"]["generated_utc"] = group["index_generated_utc"]
    assert build_upgrade_report(
        diagnostics, "2026.12", max_index_age_days=10**100, max_scan_age_days=10**100,
    )["status"] == "no_known_findings"


def test_card_does_not_inherit_same_named_integration_version():
    from custom_components.breakage_radar.report import build_report

    local_card = {"cards": {"example": {"status": "affected", "findings": [finding("2026.12")]}}}
    report = build_report(
        {}, {"example": "9.9"}, {"discovered_versions": {"example": "8.8"}},
        cards=["example"], local_card_scan=local_card,
    )
    card = next(f for f in report["details"] if f["kind"] == "card")
    assert card["installed_version"] == card["scanned_version"] == ""


def test_cli_scan_freshness_override(tmp_path, diagnostics, capsys):
    diagnostics["coverage"]["integrations"]["completed_utc"] = "2026-11-05T12:00:00Z"
    diagnostics["index"]["generated_utc"] = "2026-11-05T00:00:00Z"
    diagnostics["coverage"]["integrations"]["index_generated_utc"] = diagnostics["index"]["generated_utc"]
    path = tmp_path / "current.json"
    path.write_text(json.dumps(diagnostics), encoding="utf-8")
    args = [str(path), "--target-version", "2026.12", "--format", "json"]
    assert main(args) == 2
    assert main(args + ["--max-scan-age-days", "2"]) == 0
    capsys.readouterr()


def test_export_preserves_live_coordinator_health_without_aliasing():
    from custom_components.breakage_radar.const import DOMAIN
    from custom_components.breakage_radar.diagnostics import async_get_config_entry_diagnostics

    coordinator = SimpleNamespace(
        data={}, index_url="https://example.org/index.json", alert_window_days=30,
        last_update_success=False, last_error="index failed", _scan_lock=SimpleNamespace(locked=lambda: True),
        scan_errors={"integrations": "scan failed", "cards": None},
    )
    hass = SimpleNamespace(data={DOMAIN: {"test": coordinator}})
    export = asyncio.run(async_get_config_entry_diagnostics(hass, SimpleNamespace(entry_id="test")))
    assert export["health"]["index_update_success"] is False
    assert export["health"]["index_error"] == "index failed"
    assert export["health"]["local_scan_in_progress"] is True
    coordinator.scan_errors["integrations"] = None
    assert export["health"]["scan_errors"]["integrations"] == "scan failed"
