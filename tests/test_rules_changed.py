"""A daily provenance-only rewrite must not land as a commit.

Committing it conflicts with every open pull request that touches the file,
and a conflicted pull request gets no `pull_request` workflow run at all.
"""

from __future__ import annotations

import json

from tools.rules_changed import main, rules_changed

BASE = {
    "schema": 1,
    "core_version": "2026.9",
    "generated_utc": "2026-08-16T03:33:36Z",
    "blog_merged_utc": "2026-08-16T03:34:01Z",
    "core_tarball_sha256": "84b2adaa",
    "counts": {"total": 143, "matchable_future": 35},
    "rules": [{"id": "a", "breaks_in": "2027.8"}],
}


def test_a_new_day_alone_is_not_a_change():
    later = {
        **BASE,
        "generated_utc": "2026-08-17T03:33:37Z",
        "blog_merged_utc": "2026-08-17T03:34:02Z",
        "core_tarball_sha256": "869ef45d",
    }
    assert rules_changed(BASE, later) is False


def test_a_real_rule_edit_is_a_change():
    edited = {**BASE, "rules": [{"id": "a", "breaks_in": "2027.9"}]}
    added = {**BASE, "rules": BASE["rules"] + [{"id": "b", "breaks_in": "2027.8"}]}
    counted = {**BASE, "counts": {"total": 144, "matchable_future": 36}}
    assert rules_changed(BASE, edited) is True
    assert rules_changed(BASE, added) is True
    assert rules_changed(BASE, counted) is True


def test_nothing_to_compare_against_counts_as_changed():
    assert rules_changed(None, BASE) is True


def test_cli_exit_codes(tmp_path):
    """0 means commit it, 1 means leave it alone. `--against` cannot resolve
    here, so the payload is uncomparable and the safe answer is to commit."""
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    assert main(["--path", str(path), "--against", "HEAD"]) == 0
    assert main(["--path", str(tmp_path / "absent.json")]) == 1
