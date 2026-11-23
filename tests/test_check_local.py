"""The author-facing self-check CLI.

Exit codes are the contract, because this is meant to run in an integration
author's own CI: 0 clean, 1 findings, 2 could not check. "Could not check"
must never be reported as clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta

import pytest

from tools.check_local import is_blocking, iter_python_files, main
from tools.rules_engine import Finding


@pytest.fixture
def local_rules(repo_root, tmp_path):
    """A rules.json holding just the legacy device tracker rule."""
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "legacy-device-tracker-platform",
                        "breaks_in": "2027.5",
                        "message": "Implements the legacy device tracker platform API.",
                        "source": "https://developers.home-assistant.io/blog/",
                        "confidence": "high",
                        "match": {
                            "type": "moduledef",
                            "names": [
                                "async_get_scanner",
                                "get_scanner",
                                "async_setup_scanner",
                                "setup_scanner",
                            ],
                            "files": ["device_tracker.py"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_true_positive_checkout_exits_1_and_names_the_line(
    fixtures_dir, local_rules, capsys
):
    code = main(
        [str(fixtures_dir / "true_positive"), "--rules", str(local_rules)]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "custom_components/fixture_tracker/device_tracker.py:12" in out
    assert "2027.5" in out
    assert "1 finding(s)." in out


def test_false_positive_checkout_exits_0(fixtures_dir, local_rules, capsys):
    code = main(
        [str(fixtures_dir / "false_positive"), "--rules", str(local_rules)]
    )
    assert code == 0
    assert "no scheduled removals" in capsys.readouterr().out


def test_a_custom_components_directory_works_directly(
    fixtures_dir, local_rules, capsys
):
    code = main(
        [
            str(fixtures_dir / "true_positive" / "custom_components"),
            "--rules",
            str(local_rules),
        ]
    )
    assert code == 1
    assert "device_tracker.py:12" in capsys.readouterr().out


def test_a_directory_with_no_custom_components_cannot_be_checked(
    tmp_path, local_rules
):
    assert main([str(tmp_path), "--rules", str(local_rules)]) == 2


def test_a_missing_rules_file_cannot_be_checked(fixtures_dir, tmp_path):
    code = main(
        [str(fixtures_dir / "true_positive"), "--rules", str(tmp_path / "nope.json")]
    )
    assert code == 2


def test_a_ruleset_with_nothing_left_to_check_is_not_clean(
    fixtures_dir, local_rules
):
    """Every rule already in the past means the check proved nothing -- that is
    exit 2, never a clean 0."""
    code = main(
        [
            str(fixtures_dir / "true_positive"),
            "--rules",
            str(local_rules),
            "--ha-version",
            "2027.9",
        ]
    )
    assert code == 2


def test_ha_version_before_the_deadline_still_reports(fixtures_dir, local_rules):
    code = main(
        [
            str(fixtures_dir / "true_positive"),
            "--rules",
            str(local_rules),
            "--ha-version",
            "2027.4",
        ]
    )
    assert code == 1


def test_an_unreachable_index_cannot_be_checked(fixtures_dir):
    code = main(
        [
            str(fixtures_dir / "true_positive"),
            "--index",
            "https://127.0.0.1:9/does-not-exist.json",
        ]
    )
    assert code == 2


def test_the_walk_skips_dotted_directories_but_not_dotted_files(tmp_path):
    """The crawler's tarball reader filters vendored paths and nothing else, so
    it reads a dotted *file*. Skipping it here would make a local check
    disagree with the index about the same repository. A dotted *directory* is
    not source either way.

    Whether a rule then fires on it is the matcher's business: the shipped
    device-tracker rule constrains `files` to the exact basename
    `device_tracker.py`, so it would not match `.device_tracker.py`.
    """
    domain = tmp_path / "dotty"
    (domain / ".hidden").mkdir(parents=True)
    (domain / "node_modules").mkdir()
    for relative in (
        "sensor.py",
        ".generated.py",
        ".hidden/sensor.py",
        "node_modules/dep.py",
    ):
        (domain / relative).write_text("x = 1\n", encoding="utf-8")

    walked = [relative for _path, relative in iter_python_files(domain)]
    assert walked == [".generated.py", "sensor.py"]


# --------------------------------------------------------------------------- #
# Card repositories
# --------------------------------------------------------------------------- #


@pytest.fixture
def card_rules(tmp_path):
    """The two device-registry WebSocket fields the card fixtures read."""
    path = tmp_path / "card-rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": f"device-registry-{token.replace('_', '-')}-field",
                        "breaks_in": "2027.8",
                        "message": f"Reads {token} from a device registry result.",
                        "source": "https://developers.home-assistant.io/blog/",
                        "confidence": "high",
                        "match": {"type": "js", "token": token},
                    }
                    for token in ("config_entries", "primary_config_entry")
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_card_repository_is_scanned_without_custom_components(
    fixtures_dir, card_rules, capsys
):
    """748 of the catalogue's repositories are cards. Exit 2 for all of them
    would make the action useless to every one."""
    code = main(
        [str(fixtures_dir / "plugins" / "config_entries_card"), "--rules", str(card_rules)]
    )
    assert code == 1
    assert "power-card.js" in capsys.readouterr().out


def test_a_card_shipping_source_and_bundle_reports_the_source_once(
    fixtures_dir, card_rules, capsys
):
    code = main(
        [str(fixtures_dir / "plugins" / "ts_plus_bundle"), "--rules", str(card_rules)]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "1 finding(s)." in out
    assert "src/card.ts" in out
    assert "dist/card.js" not in out


def test_a_repository_of_nothing_but_a_bundle_is_not_clean(
    fixtures_dir, card_rules
):
    """Its only file is skipped, so nothing was analysed. That is exit 2."""
    assert (
        main([str(fixtures_dir / "plugins" / "minified_card"), "--rules", str(card_rules)])
        == 2
    )


# --------------------------------------------------------------------------- #
# --fail-on
# --------------------------------------------------------------------------- #


def _finding(breaks_in: str) -> Finding:
    return Finding(
        rule_id="r", breaks_in=breaks_in, file="f.py", line=1, confidence="high"
    )


def test_fail_on_never_reports_without_failing(fixtures_dir, local_rules, capsys):
    code = main(
        [str(fixtures_dir / "true_positive"), "--rules", str(local_rules), "--fail-on", "never"]
    )
    assert code == 0
    assert "1 finding(s)." in capsys.readouterr().out


def test_fail_on_any_is_the_default_release_gate():
    assert is_blocking(_finding("2099.1"), "any", 90, date(2026, 8, 24))


def test_fail_on_imminent_uses_the_window():
    today = date(2026, 8, 24)
    # 2026.10 lands 7 October 2026, 44 days out; 2027.8 is years away.
    assert is_blocking(_finding("2026.10"), "imminent", 90, today)
    assert not is_blocking(_finding("2027.8"), "imminent", 90, today)
    assert not is_blocking(_finding("2026.10"), "imminent", 30, today)


def test_fail_on_imminent_covers_a_release_that_already_landed():
    assert is_blocking(_finding("2026.1"), "imminent", 90, date(2026, 8, 24))


def test_fail_on_imminent_forms_no_opinion_about_an_undated_label():
    """Same refusal to guess as the integration: reported, never fatal."""
    assert not is_blocking(_finding("next"), "imminent", 3650, date(2026, 8, 24))


def test_an_imminent_finding_fails_the_job_end_to_end(fixtures_dir, tmp_path, capsys):
    """Dated from today so the assertion cannot rot."""
    soon = date.today().replace(day=1) + timedelta(days=40)
    rules = tmp_path / "soon.json"
    rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "legacy-device-tracker-platform",
                        "breaks_in": f"{soon.year}.{soon.month}",
                        "message": "Implements the legacy device tracker platform API.",
                        "confidence": "high",
                        "match": {
                            "type": "moduledef",
                            "names": ["setup_scanner"],
                            "files": ["device_tracker.py"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = [str(fixtures_dir / "true_positive"), "--rules", str(rules)]
    assert main([*args, "--fail-on", "imminent"]) == 1
    assert main([*args, "--fail-on", "imminent", "--window-days", "1"]) == 0
    assert "finding(s)." in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# --format json
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("policy, expected_code", [("any", 1), ("never", 0)])
def test_json_findings_preserve_exit_policy(fixtures_dir, local_rules, capsys, policy, expected_code):
    code = main([
        str(fixtures_dir / "true_positive"), "--rules", str(local_rules),
        "--format", "json", "--fail-on", policy,
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == expected_code
    assert report["schema"] == 1
    assert report["complete"] is True
    assert report["blocking_count"] == expected_code
    finding, = report["findings"]
    assert finding["file"] == "custom_components/fixture_tracker/device_tracker.py"
    assert finding["line"] == 12
    assert finding["message"] == "Implements the legacy device tracker platform API."
    assert finding["source"].startswith("https://")
    assert finding["blocking"] is (policy == "any")


def test_json_clean_report_is_valid_json(fixtures_dir, local_rules, capsys):
    code = main([
        str(fixtures_dir / "false_positive"), "--rules", str(local_rules), "--format", "json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == 0
    assert report["complete"] is True
    assert report["findings"] == []
    assert report["files_scanned"] > 0


def test_json_nothing_scannable_is_not_clean(fixtures_dir, card_rules, capsys):
    code = main([
        str(fixtures_dir / "plugins" / "minified_card"),
        "--rules", str(card_rules), "--format", "json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == 2
    assert report["complete"] is False
    assert report["skipped"]["minified"] > 0


@pytest.mark.parametrize(
    "target, rules_fixture",
    [("true_positive", "card_rules"), ("plugins/config_entries_card", "local_rules")],
)
def test_no_matchers_for_source_language_is_not_clean(
    fixtures_dir, target, rules_fixture, request, capsys
):
    rules = request.getfixturevalue(rules_fixture)
    code = main([str(fixtures_dir / target), "--rules", str(rules), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == 2
    assert report["complete"] is False
    assert report["files_scanned"] == 0
    assert report["skipped"]["no_matchers"] > 0


@pytest.mark.parametrize("strict", [False, True])
def test_language_coverage_uses_rules_after_version_filter(tmp_path, local_rules, card_rules, capsys, strict):
    rules = json.loads(local_rules.read_text(encoding="utf-8"))
    rules["rules"].extend(json.loads(card_rules.read_text(encoding="utf-8"))["rules"])
    local_rules.write_text(json.dumps(rules), encoding="utf-8")
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "device_tracker.py").write_text("def setup_scanner():\n    pass\n", encoding="utf-8")
    (component / "card.js").write_text(
        'hass.callWS({type: "config/device_registry/list"});\nconsole.log(device.config_entries);\n',
        encoding="utf-8",
    )
    args = [
        str(tmp_path), "--rules", str(local_rules), "--ha-version", "2027.5", "--format", "json",
        *(["--require-complete"] if strict else []),
    ]
    code = main(args)
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == (2 if strict else 1)
    assert report["complete"] is False
    assert report["files_scanned"] == 1
    assert report["skipped"]["no_matchers"] == 1
    assert report["blocking_count"] == 1
    assert report["findings"][0]["file"].endswith("/card.js")


def test_json_partial_scan_exposes_parse_failures(tmp_path, local_rules, capsys):
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (component / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    code = main([str(tmp_path), "--rules", str(local_rules), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == 0
    assert report["complete"] is False
    assert report["unparsed_files"] == 1
    assert "bad.py" in report["syntax_errors"][0]


@pytest.mark.parametrize("limit, expected", [("MAX_FILES", "capped"), ("MAX_BYTES", "too_big")])
@pytest.mark.parametrize("require_complete", [False, True])
def test_json_exposes_size_caps(
    tmp_path, local_rules, capsys, monkeypatch, limit, expected, require_complete
):
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "a.py").write_text("X = 1\n", encoding="utf-8")
    (component / "b.py").write_text("LONG_NAME = 1\n", encoding="utf-8")
    monkeypatch.setattr(f"tools.check_local.{limit}", 1 if limit == "MAX_FILES" else 10)
    code = main([
        str(tmp_path), "--rules", str(local_rules), "--format", "json",
        *(["--require-complete"] if require_complete else []),
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == (2 if require_complete else 0)
    assert report["complete"] is False
    assert report["files_scanned"] == 1
    assert report["skipped"][expected] == 1


def test_json_pre_scan_errors_have_no_stdout(tmp_path, local_rules, capsys):
    code = main([str(tmp_path / "missing"), "--rules", str(local_rules), "--format", "json"])
    assert code == 2
    assert capsys.readouterr().out == ""


def test_json_does_not_hide_unreadable_directories(tmp_path, local_rules, capsys, monkeypatch):
    component = tmp_path / "custom_components" / "example"
    blocked = component / "blocked"
    blocked.mkdir(parents=True)
    (component / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (blocked / "hidden.py").write_text("VALUE = 2\n", encoding="utf-8")
    real_scandir = os.scandir

    def scandir(path):
        if str(path) == str(blocked):
            raise PermissionError("test directory is unreadable", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert main([str(tmp_path), "--rules", str(local_rules), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False
    assert report["skipped"]["unreadable_directories"] > 0


def test_card_json_does_not_hide_unreadable_directories(tmp_path, card_rules, capsys, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (tmp_path / "good.js").write_text("const value = 1;\n", encoding="utf-8")
    real_scandir = os.scandir

    def scandir(path):
        if str(path) == str(blocked):
            raise PermissionError("test directory is unreadable", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert main([
        str(tmp_path), "--rules", str(card_rules), "--format", "json", "--require-complete",
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False
    assert report["skipped"]["unreadable_directories"] == 1


@pytest.mark.parametrize("payload", [
    None, 3, "invalid", {"rules": None}, {"rules": {}}, {"rules": [None]},
    {"rules": [{"id": "missing-release"}]},
    {"rules": [{"id": "bad-match", "breaks_in": "2027.5", "match": "invalid"}]},
    {"rules": [{"id": "bad-release", "breaks_in": 2027}]},
    {"rules": [{"id": "bad-message", "breaks_in": "2027.5", "message": []}]},
])
def test_malformed_rules_exit_2_without_a_traceback(tmp_path, fixtures_dir, payload):
    rules = tmp_path / "malformed.json"
    rules.write_text(json.dumps(payload), encoding="utf-8")
    assert main([
        str(fixtures_dir / "true_positive"), "--rules", str(rules), "--format", "json",
    ]) == 2


def test_duplicate_rule_ids_are_rejected(fixtures_dir, local_rules):
    payload = json.loads(local_rules.read_text(encoding="utf-8"))
    payload["rules"].append(payload["rules"][0])
    local_rules.write_text(json.dumps(payload), encoding="utf-8")
    assert main([
        str(fixtures_dir / "true_positive"), "--rules", str(local_rules), "--format", "json",
    ]) == 2


def test_non_object_index_is_rejected(fixtures_dir, monkeypatch):
    monkeypatch.setattr("tools.check_local.http_get_json", lambda _url: [])
    assert main([str(fixtures_dir / "true_positive"), "--format", "json"]) == 2


def test_a_rules_directory_is_an_input_error(fixtures_dir, tmp_path):
    assert main([str(fixtures_dir / "true_positive"), "--rules", str(tmp_path)]) == 2


def test_bare_rules_array_still_works(fixtures_dir, local_rules, capsys):
    payload = json.loads(local_rules.read_text(encoding="utf-8"))
    local_rules.write_text(json.dumps(payload["rules"]), encoding="utf-8")
    assert main([
        str(fixtures_dir / "true_positive"), "--rules", str(local_rules), "--format", "json",
    ]) == 1
    assert len(json.loads(capsys.readouterr().out)["findings"]) == 1


def test_json_script_keeps_logs_on_stderr(repo_root, fixtures_dir, local_rules):
    result = subprocess.run([
        sys.executable, str(repo_root / "tools" / "check_local.py"),
        str(fixtures_dir / "true_positive"), "--rules", str(local_rules),
        "--format", "json", "--require-complete",
    ], capture_output=True, text=True, check=False, timeout=20)
    report = json.loads(result.stdout)
    assert result.returncode == report["exit_code"] == 1
    assert report["complete"] is True
    assert "matchable rule(s)" in result.stderr


@pytest.mark.parametrize("output_format", ["text", "github", "json"])
@pytest.mark.parametrize("policy", ["any", "never"])
def test_require_complete_rejects_partial_scans(
    tmp_path, local_rules, capsys, output_format, policy
):
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (component / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    code = main([
        str(tmp_path), "--rules", str(local_rules), "--format", output_format,
        "--fail-on", policy, "--require-complete",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "OK" not in out
    if output_format == "json":
        report = json.loads(out)
        assert report["exit_code"] == 2
        assert report["complete"] is False


@pytest.mark.parametrize("policy, expected", [("any", 1), ("never", 0), ("imminent", 0)])
def test_require_complete_preserves_finding_policy(
    fixtures_dir, local_rules, capsys, policy, expected
):
    future = date.today() + timedelta(days=400)
    payload = json.loads(local_rules.read_text(encoding="utf-8"))
    payload["rules"][0]["breaks_in"] = f"{future.year}.{future.month}"
    local_rules.write_text(json.dumps(payload), encoding="utf-8")
    code = main([
        str(fixtures_dir / "true_positive"), "--rules", str(local_rules),
        "--format", "json", "--fail-on", policy, "--window-days", "0", "--require-complete",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == expected
    assert report["complete"] is True


def test_partial_default_text_does_not_claim_ok(tmp_path, local_rules, capsys):
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (component / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    assert main([str(tmp_path), "--rules", str(local_rules)]) == 0
    out = capsys.readouterr().out
    assert "scan incomplete" in out
    assert "OK" not in out


@pytest.mark.parametrize("policy", ["any", "never"])
def test_incomplete_gate_preserves_findings(tmp_path, local_rules, capsys, policy):
    component = tmp_path / "custom_components" / "example"
    component.mkdir(parents=True)
    (component / "device_tracker.py").write_text(
        "def setup_scanner(hass, config, see):\n    return True\n", encoding="utf-8"
    )
    (component / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    code = main([
        str(tmp_path), "--rules", str(local_rules), "--format", "json",
        "--require-complete", "--fail-on", policy,
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == report["exit_code"] == 2
    assert report["complete"] is False
    assert len(report["findings"]) == 1
    assert report["blocking_count"] == (1 if policy == "any" else 0)


def test_negative_window_is_rejected_before_scanning():
    with pytest.raises(SystemExit) as error:
        main(["--window-days", "-1"])
    assert error.value.code == 2


# --------------------------------------------------------------------------- #
# --format github
# --------------------------------------------------------------------------- #


def test_github_format_annotates_the_exact_line(
    fixtures_dir, local_rules, tmp_path, monkeypatch, capsys
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = main(
        [
            str(fixtures_dir / "true_positive"),
            "--rules",
            str(local_rules),
            "--format",
            "github",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "::error file=custom_components/fixture_tracker/device_tracker.py," in out
    assert "line=12," in out
    assert "title=Breaks in Home Assistant 2027.5" in out
    # A blocking finding is an error, so it survives the 10-warnings cap on its
    # own budget.
    assert "::warning" not in out


def test_github_format_downgrades_what_will_not_fail_the_job(
    fixtures_dir, local_rules, capsys
):
    main(
        [
            str(fixtures_dir / "true_positive"),
            "--rules",
            str(local_rules),
            "--format",
            "github",
            "--fail-on",
            "never",
        ]
    )
    out = capsys.readouterr().out
    assert "::warning file=" in out
    assert "::error" not in out


def test_github_format_writes_every_finding_to_the_job_summary(
    fixtures_dir, card_rules, tmp_path, monkeypatch
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(
        [
            str(fixtures_dir / "plugins" / "config_entries_card"),
            "--rules",
            str(card_rules),
            "--format",
            "github",
        ]
    )
    written = summary.read_text(encoding="utf-8")
    assert "## Home Assistant Breakage Radar" in written
    assert "| Breaks in | When | File | Rule | Confidence |" in written
    assert "`power-card.js:" in written
    assert "not a guarantee of breakage" in written


def test_github_format_survives_no_summary_file(fixtures_dir, local_rules, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert (
        main(
            [
                str(fixtures_dir / "true_positive"),
                "--rules",
                str(local_rules),
                "--format",
                "github",
            ]
        )
        == 1
    )


def test_clean_github_check_writes_a_summary(fixtures_dir, local_rules, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main([
        str(fixtures_dir / "false_positive"), "--rules", str(local_rules),
        "--format", "github", "--require-complete",
    ]) == 0
    assert "0 finding(s) in this checkout." in summary.read_text(encoding="utf-8")


@pytest.fixture
def vacuum_rules(tmp_path):
    """The scoped rule ``tools/extract_rules.py`` derives from core 2026.8."""
    path = tmp_path / "vacuum_rules.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "core-attr-statevacuumentity-battery-level",
                        "breaks_in": "2026.9",
                        "symbol": "StateVacuumEntity.battery_level",
                        "message": (
                            "defines `battery_level` on a subclass of "
                            "`StateVacuumEntity`, which is deprecated and removed "
                            "in Home Assistant 2026.9."
                        ),
                        "source": "homeassistant/components/vacuum/__init__.py:269",
                        "confidence": "medium",
                        "match": {
                            "type": "attr",
                            "names": ["battery_level"],
                            "in_class_base": ["StateVacuumEntity"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_scoped_vacuum_property_is_flagged_and_names_its_base_class(
    fixtures_dir, vacuum_rules, capsys
):
    code = main([str(fixtures_dir / "scoped_attr"), "--rules", str(vacuum_rules)])
    out = capsys.readouterr().out
    assert code == 1
    assert "custom_components/fixture_vacuum/vacuum.py:29" in out
    assert "battery_level" in out and "StateVacuumEntity" in out
    assert out.count("custom_components/fixture_vacuum/vacuum.py") == 1


def test_the_check_states_what_it_could_not_look_for(fixtures_dir, tmp_path, caplog):
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "counts": {"markers_discarded": 3, "markers_discarded_pending": 2},
                "rules": [
                    {
                        "id": "matchable",
                        "breaks_in": "2027.5",
                        "message": "m",
                        "source": "s",
                        "match": {"type": "moduledef", "names": ["nothing_here"]},
                    },
                    {"id": "prose-only", "breaks_in": "2027.5", "message": "m", "source": "s"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("INFO"):
        main([str(fixtures_dir / "false_positive"), "--rules", str(rules)])
    assert "1 of 2 announced removals have a matcher" in caplog.text
    assert "2 marker(s) too vague to match" in caplog.text


# --------------------------------------------------------------------------- #
# Deprecations that warn before they break
# --------------------------------------------------------------------------- #


@pytest.fixture
def warning_rules(local_rules, tmp_path):
    """The legacy tracker rule again, but warning two years before removal,
    alongside one that breaks sooner on an earlier line of the same file."""
    path = tmp_path / "warning-rules.json"
    payload = json.loads(local_rules.read_text(encoding="utf-8"))
    payload["rules"][0].update(breaks_in="2027.10", reports_in="2026.10")
    payload["rules"].append(
        {
            "id": "const-conf-host",
            "breaks_in": "2027.9",
            "message": "Imports CONF_HOST from homeassistant.const.",
            "source": "https://developers.home-assistant.io/blog/",
            "confidence": "high",
            "match": {
                "type": "import_from",
                "modules": ["homeassistant.const"],
                "names": ["CONF_HOST"],
            },
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_check_names_the_release_the_warning_starts_in(
    fixtures_dir, warning_rules, capsys
):
    code = main([str(fixtures_dir / "true_positive"), "--rules", str(warning_rules)])
    out = capsys.readouterr().out
    assert code == 1
    assert "breaks in Home Assistant 2027.10" in out
    assert "logs a warning from Home Assistant 2026.10" in out


def test_a_rule_with_one_date_prints_one_date(fixtures_dir, local_rules, capsys):
    code = main([str(fixtures_dir / "true_positive"), "--rules", str(local_rules)])
    out = capsys.readouterr().out
    assert code == 1
    assert "logs a warning" not in out


def test_the_job_summary_carries_both_releases_in_one_cell(
    fixtures_dir, warning_rules, tmp_path, monkeypatch
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(
        [
            str(fixtures_dir / "true_positive"),
            "--rules",
            str(warning_rules),
            "--format",
            "github",
        ]
    )
    assert "2027.10 (warns from 2026.10)" in summary.read_text(encoding="utf-8")


def test_findings_are_ordered_by_release_not_by_text(
    fixtures_dir, warning_rules, capsys
):
    """2027.9 comes before 2027.10. Ordered as text it comes after, and the
    thing a maintainer has least time to fix is reported last."""
    main([str(fixtures_dir / "true_positive"), "--rules", str(warning_rules)])
    out = capsys.readouterr().out
    assert out.index("device_tracker.py:7") < out.index("device_tracker.py:12")
