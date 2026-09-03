"""The author-facing self-check CLI.

Exit codes are the contract, because this is meant to run in an integration
author's own CI: 0 clean, 1 findings, 2 could not check. "Could not check"
must never be reported as clean.
"""

from __future__ import annotations

import json
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
    assert "2 short name(s) not matched" in caplog.text
