"""Crawler scheduling, resumption and failure handling."""

from __future__ import annotations

import dataclasses
import io
import json
import logging
import tarfile

import pytest

from tools import scan as scan_module
from tools.common import NotFound, RateLimited
from tools.rules_engine import Rule, matchable_rules
from tools.scan import main, rules_hash, scan_repo, select_slice

RULE = Rule(
    id="legacy-device-tracker-platform",
    kind="moduledef",
    symbol="setup_scanner",
    message="legacy platform",
    breaks_in="2027.5",
    source="https://developers.home-assistant.io/",
    origin="manual",
    confidence="high",
    match={
        "type": "moduledef",
        "names": ["setup_scanner"],
        "files": ["device_tracker.py"],
    },
)

CATALOG = [
    {"full_name": "a/one", "domain": "one", "last_version": "1.0.0"},
    {"full_name": "b/two", "domain": "two", "last_version": "2.0.0"},
    {"full_name": "c/three", "domain": "three", "last_version": "3.0.0"},
]


def test_unscanned_repos_come_first_then_least_recently_scanned():
    state = {
        "b/two": {
            "last_version_scanned": "1.9.9",
            "rules_hash": "x",
            "last_scanned_utc": "2026-01-01T00:00:00Z",
        },
        "c/three": {
            "last_version_scanned": "2.9.9",
            "rules_hash": "x",
            "last_scanned_utc": "2025-01-01T00:00:00Z",
        },
    }
    order = [
        e["full_name"]
        for e in select_slice(CATALOG, state, limit=10, current_rules_hash="x", force=False)
    ]
    assert order == ["a/one", "c/three", "b/two"]


def test_unchanged_repos_are_skipped():
    state = {
        entry["full_name"]: {
            "last_version_scanned": entry["last_version"],
            "rules_hash": "x",
            "last_scanned_utc": "2026-01-01T00:00:00Z",
        }
        for entry in CATALOG
    }
    assert select_slice(CATALOG, state, limit=10, current_rules_hash="x", force=False) == []
    # A rules/engine change invalidates every cached result.
    assert (
        len(select_slice(CATALOG, state, limit=10, current_rules_hash="y", force=False))
        == 3
    )


def test_rules_hash_changes_with_the_engine_version(monkeypatch):
    before = rules_hash([RULE])
    monkeypatch.setattr(scan_module, "ENGINE_VERSION", 999)
    assert rules_hash([RULE]) != before


def test_a_rule_breaking_in_the_dev_release_stays_active():
    # dev carries the release being built, so 2027.5 there means nobody is
    # running it yet and the rule is at its most urgent, not expired.
    assert matchable_rules([RULE], current_version="2027.5") == [RULE]
    assert matchable_rules([RULE], current_version="2027.6") == []


def test_missing_tag_falls_back_then_marks_unreachable(monkeypatch):
    tried: list[str] = []

    def fake_http_get(url, **kwargs):
        tried.append(url)
        raise NotFound(url)

    monkeypatch.setattr(scan_module, "http_get", fake_http_get)
    record, findings = scan_repo(CATALOG[0], [RULE])
    assert record["status"] == "unreachable"
    assert findings == []
    assert [u.rsplit("/tar.gz/", 1)[1] for u in tried] == [
        "refs/tags/1.0.0",
        "refs/tags/v1.0.0",
        "refs/heads/main",
        "refs/heads/master",
    ]


def test_rate_limit_propagates_so_the_slice_can_stop(monkeypatch):
    def fake_http_get(url, **kwargs):
        raise RateLimited(url)

    monkeypatch.setattr(scan_module, "http_get", fake_http_get)
    with pytest.raises(RateLimited):
        scan_repo(CATALOG[0], [RULE])


def test_a_corrupt_tarball_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        scan_module, "http_get", lambda url, **kwargs: b"this is not a tarball"
    )
    record, findings = scan_repo(CATALOG[0], [RULE])
    assert record["status"] == "error"
    assert findings == []


def _write_inputs(tmp_path, catalog=CATALOG, rules=(RULE,), core_version="2026.9", **extra):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "core_version": core_version,
                "rules": [rule.to_dict() for rule in rules],
                **extra,
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"schema": 1, "integrations": catalog}), encoding="utf-8"
    )
    return rules_path, catalog_path


def _argv(tmp_path, rules_path, catalog_path, *extra):
    return [
        "--rules", str(rules_path),
        "--catalog", str(catalog_path),
        "--findings", str(tmp_path / "findings.json"),
        "--state", str(tmp_path / "crawl.json"),
        *extra,
    ]


def test_slice_ends_cleanly_and_commits_state_when_rate_limited(tmp_path, monkeypatch):
    rules_path, catalog_path = _write_inputs(tmp_path)
    calls = {"n": 0}

    def fake_scan_repo(entry, rules):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RateLimited("429")
        return (
            {
                "domain": entry["domain"],
                "version": entry["last_version"],
                "ref": "refs/tags/1.0.0",
                "status": "scanned",
                "scanned_utc": "2026-08-08T00:00:00Z",
                "files_scanned": 1,
                "syntax_errors": 0,
                "findings": [],
            },
            [],
        )

    monkeypatch.setattr(scan_module, "scan_repo", fake_scan_repo)
    assert main(_argv(tmp_path, rules_path, catalog_path, "--limit", "3")) == 0

    state = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    findings = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert list(state) == ["a/one"], "work done before the 429 must be kept"
    assert list(findings["repos"]) == ["a/one"]


def test_findings_for_a_retired_rule_are_dropped(tmp_path):
    """A rule retires when its release lands in dev. Only a slice of the
    catalogue is rescanned a day, so without this the leftover hits outlive the
    rule they name and build_index refuses to publish the index at all."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    (tmp_path / "findings.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "repos": {
                    "a/one": {
                        "domain": "one",
                        "status": "scanned",
                        "findings": [
                            {
                                "rule_id": "gone-in-the-last-release",
                                "breaks_in": "2026.9",
                                "file": "custom_components/one/sensor.py",
                                "line": 3,
                                "confidence": "high",
                            },
                            {
                                "rule_id": RULE.id,
                                "breaks_in": "2027.5",
                                "file": "custom_components/one/device_tracker.py",
                                "line": 7,
                                "confidence": "high",
                            },
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    # Every repository is already up to date, so nothing gets rescanned and the
    # prune is the only thing that can clear the stale hit.
    (tmp_path / "crawl.json").write_text(
        json.dumps(
            {
                entry["full_name"]: {
                    "last_version_scanned": entry["last_version"],
                    "rules_hash": rules_hash([RULE]),
                    "last_scanned_utc": "2026-08-08T00:00:00Z",
                }
                for entry in CATALOG
            }
        ),
        encoding="utf-8",
    )

    assert main(_argv(tmp_path, rules_path, catalog_path)) == 0

    findings = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert [f["rule_id"] for f in findings["repos"]["a/one"]["findings"]] == [RULE.id]


RC_RULE = dataclasses.replace(RULE, id="rc-window-rule", breaks_in="2026.9")

TRACKER_SOURCE = "def setup_scanner(hass, config, see, discovery_info=None):\n    ...\n"


def _tarball_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            member = tarfile.TarInfo(f"repo-1.0/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def _write_rc_rules(tmp_path, **extra):
    """rules.json for the #46 window: stable 2026.8, 2026.9 in RC, dev 2026.10."""
    return _write_inputs(
        tmp_path, catalog=CATALOG[:1], rules=(RC_RULE,), core_version="2026.10", **extra
    )


def test_a_rule_for_the_release_in_rc_stays_in_the_scan(tmp_path, monkeypatch):
    """Issue #46's exact scenario. Compared against dev the 2026.9 rule reads
    as shipped and vanishes in the one week a user can still act; against the
    released floor it is pending and its finding lands in the scan."""
    rules_path, catalog_path = _write_rc_rules(
        tmp_path,
        latest_release="2026.8",
        pending_floor="2026.9",
        pending_floor_source="pypi",
    )
    body = _tarball_bytes(
        {
            "custom_components/one/device_tracker.py": TRACKER_SOURCE,
            "custom_components/one/manifest.json": '{"domain": "one"}',
        }
    )
    monkeypatch.setattr(scan_module, "http_get", lambda url, **kwargs: body)

    assert main(_argv(tmp_path, rules_path, catalog_path, "--no-upstream")) == 0
    findings = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert [
        (f["rule_id"], f["breaks_in"])
        for f in findings["repos"]["a/one"]["findings"]
    ] == [("rc-window-rule", "2026.9")]


def test_the_same_rule_retires_once_the_release_ships(tmp_path):
    # A week later 2026.9 is on PyPI, the floor moves to 2026.10, and the
    # only rule left is shipped -- nothing remains to scan for.
    rules_path, catalog_path = _write_rc_rules(
        tmp_path,
        latest_release="2026.9",
        pending_floor="2026.10",
        pending_floor_source="pypi",
    )
    assert main(_argv(tmp_path, rules_path, catalog_path)) == 2


def test_an_old_rules_file_falls_back_and_says_so(tmp_path, monkeypatch, caplog):
    """No pending floor recorded (an offline run, or a rules.json from before
    it existed): dev minus one keeps the RC rule listed and the degradation is
    printed rather than silent."""
    rules_path, catalog_path = _write_rc_rules(tmp_path)
    body = _tarball_bytes(
        {
            "custom_components/one/device_tracker.py": TRACKER_SOURCE,
            "custom_components/one/manifest.json": '{"domain": "one"}',
        }
    )
    monkeypatch.setattr(scan_module, "http_get", lambda url, **kwargs: body)

    with caplog.at_level(logging.WARNING, logger="breakage_radar.tools"):
        assert main(_argv(tmp_path, rules_path, catalog_path, "--no-upstream")) == 0
    assert "dev minus one" in caplog.text
    findings = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert [f["rule_id"] for f in findings["repos"]["a/one"]["findings"]] == [
        "rc-window-rule"
    ]


def test_missing_inputs_exit_with_a_clear_code(tmp_path):
    assert main(_argv(tmp_path, tmp_path / "no-rules.json", tmp_path / "no-cat.json")) == 2


def test_no_matchable_rules_refuses_to_scan(tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "core_version": "2026.9",
                "rules": [{**RULE.to_dict(), "breaks_in": "2020.1"}],
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"schema": 1, "integrations": CATALOG}), encoding="utf-8"
    )
    assert main(_argv(tmp_path, rules_path, catalog_path)) == 2


def test_only_filter_rejects_an_unknown_repo(tmp_path):
    rules_path, catalog_path = _write_inputs(tmp_path)
    assert (
        main(_argv(tmp_path, rules_path, catalog_path, "--only", "nobody/nothing")) == 2
    )
