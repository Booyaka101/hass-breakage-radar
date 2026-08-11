"""The author-facing self-check CLI.

Exit codes are the contract, because this is meant to run in an integration
author's own CI: 0 clean, 1 findings, 2 could not check. "Could not check"
must never be reported as clean.
"""

from __future__ import annotations

import json

import pytest

from tools.check_local import main


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
