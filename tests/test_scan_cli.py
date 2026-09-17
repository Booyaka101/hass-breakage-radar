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
from tools.scan import (
    main,
    rules_hash,
    scan_repo,
    select_slice,
    upstream_still_applies,
)
from tools.upstream import FACT_MAX_AGE_DAYS, LOOKUP_LIMIT

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

    def fake_scan_repo(entry, rules, fetched=None):
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


def _findings_doc(upstream: dict, findings: list[dict]) -> str:
    return json.dumps(
        {
            "schema": 1,
            "repos": {
                "a/one": {
                    "domain": "one",
                    "status": "scanned",
                    "findings": findings,
                    "upstream": upstream,
                }
            },
        }
    )


UPSTREAM = {
    "archived": False,
    "issues_enabled": True,
    "symbol": "setup_scanner",
    "report": {"number": 12, "url": "https://example.invalid/12", "state": "open"},
}

SAME_FINDING = [
    {
        "rule_id": RULE.id,
        "breaks_in": "2027.5",
        "file": "custom_components/one/device_tracker.py",
        "line": 1,
        "confidence": "high",
    }
]


def _rescan_one(tmp_path, monkeypatch, findings: list[dict], upstream=None) -> dict:
    """Force a rescan of a/one over a record holding ``findings``, and return it."""
    rules_path, catalog_path = _write_inputs(tmp_path, catalog=CATALOG[:1])
    (tmp_path / "findings.json").write_text(
        _findings_doc(upstream or UPSTREAM, findings), encoding="utf-8"
    )
    monkeypatch.setattr(
        scan_module,
        "http_get",
        lambda url, **kwargs: _tarball_bytes(
            {"custom_components/one/device_tracker.py": TRACKER_SOURCE}
        ),
    )
    assert main(_argv(tmp_path, rules_path, catalog_path, "--no-upstream")) == 0
    doc = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    return doc["repos"]["a/one"]


def test_an_unchanged_rescan_keeps_the_upstream_report(tmp_path, monkeypatch):
    """An engine bump requeues every repo; the report it already found upstream
    is about the deprecation, not about when the scan ran."""
    assert _rescan_one(tmp_path, monkeypatch, SAME_FINDING)["upstream"] == UPSTREAM


def test_a_rescan_that_no_longer_trips_the_symbol_drops_the_upstream_report(
    tmp_path, monkeypatch
):
    """Carrying it forward would keep linking people to an issue about an API
    the repository has already migrated off."""
    other = {**UPSTREAM, "symbol": "async_import_statistics"}
    assert "upstream" not in _rescan_one(
        tmp_path, monkeypatch, SAME_FINDING, upstream=other
    )


def test_the_pending_floor_reaches_the_upstream_lookup(tmp_path, monkeypatch):
    """Without a release to compare against, every issue title naming one
    counts, including the 2021.12 and 2022.11 ones that were published as
    reports. It is the floor and not dev: during the RC window a title about
    the release being cut is about something nobody is running yet."""
    rules_path, catalog_path = _write_inputs(
        tmp_path,
        catalog=CATALOG[:1],
        core_version="2026.10",
        latest_release="2026.8",
        pending_floor="2026.9",
        pending_floor_source="pypi",
    )
    monkeypatch.setattr(
        scan_module,
        "http_get",
        lambda url, **kwargs: _tarball_bytes(
            {"custom_components/one/device_tracker.py": TRACKER_SOURCE}
        ),
    )
    seen: dict[str, object] = {}

    def fake_annotate(records, rules_by_id, **kwargs):
        seen.update(kwargs)
        seen["rules"] = rules_by_id
        return 0

    monkeypatch.setattr(scan_module, "annotate", fake_annotate)
    assert main(_argv(tmp_path, rules_path, catalog_path)) == 0
    assert seen["current_version"] == "2026.9"
    assert seen["rules"][RULE.id] == {"symbol": "setup_scanner", "search": None}


def _found_nothing(entry, rules, fetched=None):
    """A scan of a repository with nothing wrong in it."""
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


def test_a_repository_outside_the_slice_is_still_offered_for_a_lookup(
    tmp_path, monkeypatch
):
    """A repository that cuts no release is never in a slice again, so a
    lookup over the slice alone left its recorded issue published for good,
    however wrong the fact had gone."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    (tmp_path / "findings.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "repos": {
                    name: {
                        "domain": name.split("/")[1],
                        "status": "scanned",
                        "findings": [
                            {
                                "rule_id": RULE.id,
                                "breaks_in": "2027.5",
                                "file": f"custom_components/{name.split('/')[1]}/device_tracker.py",
                                "line": 7,
                                "confidence": "high",
                            }
                        ],
                    }
                    # z/delisted is published while it has findings, catalogue
                    # or no catalogue, so its fact has to age like the rest.
                    for name in ("b/two", "z/delisted")
                },
            }
        ),
        encoding="utf-8",
    )

    offered: list[str] = []

    def fake_annotate(records, rules_by_id, **kwargs):
        offered.extend(sorted(records))
        return 0

    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)
    monkeypatch.setattr(scan_module, "annotate", fake_annotate)
    assert main(_argv(tmp_path, rules_path, catalog_path, "--limit", "1")) == 0
    assert offered == ["a/one", "b/two", "z/delisted"]


def test_one_limit_covers_the_scan_and_the_lookups(tmp_path, monkeypatch):
    """--limit is how much work a run does. Left to its own default, a
    --limit 5 smoke test scanned five repositories and then spent a quarter of
    an hour on four hundred lookups. It buys lookups up to the cap only: a
    full rescan asks for thousands of repositories, and thousands of lookups
    at 2.1 seconds apart outlast the job they run in."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    budget: list[int] = []
    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)
    monkeypatch.setattr(
        scan_module,
        "annotate",
        lambda records, rules, **kwargs: budget.append(kwargs["limit"]),
    )
    assert main(_argv(tmp_path, rules_path, catalog_path, "--limit", "1")) == 0
    assert budget == [1]
    assert main(_argv(tmp_path, rules_path, catalog_path, "--limit", "4000")) == 0
    assert budget == [1, LOOKUP_LIMIT]


def test_naming_a_repository_asks_about_it_however_young_its_fact_is(
    tmp_path, monkeypatch
):
    """A forced rescan carries the fact forward with its old timestamp, so the
    freshness gate answered nothing at all to `--only owner/repo`, which is the
    one command whose whole point is that repository."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    ages: list[int] = []
    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)
    monkeypatch.setattr(
        scan_module,
        "annotate",
        lambda records, rules, **kwargs: ages.append(kwargs["max_age_days"]),
    )
    assert main(_argv(tmp_path, rules_path, catalog_path)) == 0
    assert main(_argv(tmp_path, rules_path, catalog_path, "--only", "a/one")) == 0
    assert ages == [FACT_MAX_AGE_DAYS, 0]


def test_a_crawl_killed_during_the_lookups_keeps_the_ones_it_made(
    tmp_path, monkeypatch
):
    """The runner's timeout covers the lookups too, and they are the slowest
    part of a quiet day: 2.1 seconds apart, up to four hundred of them."""
    rules_path, catalog_path = _write_inputs(tmp_path)

    def annotate(records, rules, *, checkpoint, **kwargs):
        records["a/one"]["upstream"] = {"symbol": "setup_scanner", "archived": True}
        checkpoint()
        raise TimeoutError("runner cancelled the job")

    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)
    monkeypatch.setattr(scan_module, "annotate", annotate)
    with pytest.raises(TimeoutError):
        main(_argv(tmp_path, rules_path, catalog_path))
    saved = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert saved["repos"]["a/one"]["upstream"]["archived"] is True


def test_a_day_with_nothing_to_scan_still_refreshes_the_facts(tmp_path, monkeypatch):
    """Most days the slice is empty: 4 009 of the 4 021 state entries are
    already current. Returning early there is a week with no refresh at all."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)
    runs: list[int] = []
    monkeypatch.setattr(
        scan_module, "annotate", lambda records, rules, **kw: runs.append(len(records))
    )
    argv = _argv(tmp_path, rules_path, catalog_path)
    assert main(argv) == 0
    assert main(argv) == 0, "the second run has nothing left to scan"
    assert len(runs) == 2


def test_a_fact_dropped_on_a_quiet_day_is_dropped_on_disk(tmp_path, monkeypatch):
    """A repository whose findings are all gone loses its upstream fact, and
    that happens whether or not the run had a lookup to spend. Most days it has
    none, and the stale fact was written back untouched."""
    rules_path, catalog_path = _write_inputs(tmp_path)
    monkeypatch.setattr(scan_module, "scan_repo", _found_nothing)

    def records_the_fact(records, rules, **kwargs):
        records["a/one"]["upstream"] = {"symbol": "setup_scanner", "archived": True}
        return 1

    def drops_the_fact(records, rules, **kwargs):
        records["a/one"].pop("upstream", None)
        return 0

    argv = _argv(tmp_path, rules_path, catalog_path)
    monkeypatch.setattr(scan_module, "annotate", records_the_fact)
    assert main(argv) == 0
    monkeypatch.setattr(scan_module, "annotate", drops_the_fact)
    assert main(argv) == 0
    saved = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert "upstream" not in saved["repos"]["a/one"]


def test_an_older_interpreter_than_the_extractor_is_warned_about(monkeypatch, caplog):
    """1 519 files failed to parse on the 1.12.0 rescan because it ran on 3.11
    against rules extracted on 3.14, and the findings in them vanished without
    a word. The interpreter mismatch is the thing to say."""
    import logging

    from tools.scan import warn_if_older_python

    caplog.set_level(logging.WARNING, logger="breakage_radar.tools")
    monkeypatch.setattr(scan_module.sys, "version_info", (3, 11, 9, "final", 0))
    assert warn_if_older_python("3.14") is True
    assert "extracted with 3.14" in caplog.text
    assert warn_if_older_python("3.11") is False
    assert warn_if_older_python(None) is False
    assert warn_if_older_python("garbage") is False


def test_a_rule_renamed_for_the_same_symbol_keeps_the_upstream_report(
    tmp_path, monkeypatch
):
    """A hand-written rule superseding core's own changes the rule id but not
    the API. The issue the repo already filed is about the API."""
    renamed = [{**SAME_FINDING[0], "rule_id": "core-moduledef-setup-scanner"}]
    assert _rescan_one(tmp_path, monkeypatch, renamed)["upstream"] == UPSTREAM


def test_a_rule_breaking_sooner_does_not_discard_the_fact():
    """The fact is about a symbol the repository uses, not about which of its
    deprecations happens to break first."""
    symbols = {"soon": {"symbol": "other"}, "later": {"symbol": "setup_scanner"}}
    findings = [
        {"rule_id": "soon", "breaks_in": "2027.8"},
        {"rule_id": "later", "breaks_in": "2027.10"},
    ]
    assert upstream_still_applies(UPSTREAM, findings, symbols) is True


def test_a_repository_that_fixed_the_symbol_loses_the_fact():
    symbols = {"soon": {"symbol": "other"}}
    findings = [{"rule_id": "soon", "breaks_in": "2027.8"}]
    assert upstream_still_applies(UPSTREAM, findings, symbols) is False


def test_no_findings_leaves_nothing_for_an_upstream_fact_to_be_about():
    assert upstream_still_applies(UPSTREAM, [], {}) is False


def test_a_fact_is_kept_against_the_term_the_rule_asked_for():
    """The fact is filed under what the repository was searched for. When a
    rule overrides that term, a fact filed under the old one is stale and gets
    looked up again."""
    rules = {
        "mapping": {
            "symbol": "DeviceRegistry.devices",
            "search": "device_registry.devices",
        }
    }
    findings = [{"rule_id": "mapping", "breaks_in": "2027.9"}]
    overridden = {**UPSTREAM, "symbol": "device_registry.devices"}
    assert upstream_still_applies(overridden, findings, rules) is True
    assert upstream_still_applies({**UPSTREAM, "symbol": "devices"}, findings, rules) is False
