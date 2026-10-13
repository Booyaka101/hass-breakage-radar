"""The owner-requested withdrawal list (issue #65).

``Kaluzaburza/hoymiles-hit-g3-ems`` -- scanned two months earlier as
``Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus`` -- was withdrawn from public
distribution and its owner asked this project to drop it from the catalogue,
the published index, and any cached copy. HACS's own removal was still in
review at the time, so the data-v2 feed kept listing the repository: waiting
for the feed to do it would have had the crawl re-queue it as never-scanned
the next morning and re-add it to the board as unreachable.

The list therefore stands on its own: ``data/excluded.json`` drops the
catalogue entry, purges findings and crawl state under every name the
repository carried, deletes cached tarballs, and keeps a stale findings
record out of the built index.
"""

from __future__ import annotations

import json

from test_build_index import CATALOG_DOC, RULES_DOC
from test_scan_cli import RULE as SCAN_RULE

from tools.build_index import build_payload, validate_index
from tools.catalog import apply_exclusions
from tools.catalog import main as catalog_main
from tools.common import Exclusion, load_excluded
from tools.scan import cache_path, purge_cached_tarballs
from tools.scan import main as scan_main

WITHDRAWN = {
    "full_name": "Kaluzaburza/hoymiles-hit-g3-ems",
    "former_names": ["Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus"],
    "domain": "hoymiles_hit_modbus",
    "repo_id": "1311289521",
    "reason": "Owner withdrew the integration from public distribution.",
    "reference": "https://github.com/Booyaka101/hass-breakage-radar/issues/65",
    "excluded_utc": "2026-10-13T12:00:00Z",
}


def _excluded_file(tmp_path):
    path = tmp_path / "excluded.json"
    path.write_text(
        json.dumps({"schema": 1, "repositories": [WITHDRAWN]}), encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


def test_a_repository_matches_under_its_former_name():
    """A rename leaves the old name behind in findings and crawl state, so the
    exclusion has to reach back through former_names to catch both records."""
    exclusion = Exclusion([WITHDRAWN])
    assert exclusion.record_for("Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus") is WITHDRAWN
    assert exclusion.record_for("Kaluzaburza/hoymiles-hit-g3-ems") is WITHDRAWN


def test_a_repository_matches_by_id_and_by_domain():
    """The repo id and the integration domain are what a rename or a transfer
    cannot shake off -- the domain is what the published index keys on."""
    exclusion = Exclusion([WITHDRAWN])
    by_id = exclusion.record_for("whoever/renamed-again", {"repo_id": "1311289521"})
    assert by_id is WITHDRAWN
    by_domain = exclusion.record_for(
        "whoever/renamed-again", {"domain": "hoymiles_hit_modbus"}
    )
    assert by_domain is WITHDRAWN
    assert exclusion.record_for("someone/untouched") is None


def test_an_unrelated_repository_does_not_match():
    exclusion = Exclusion([WITHDRAWN])
    assert exclusion.record_for("a/one", {"domain": "one", "repo_id": "7"}) is None


def test_a_missing_file_excludes_nothing(tmp_path):
    assert load_excluded(tmp_path / "not-there.json") == []
    assert Exclusion([]).names() == set()


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #


def test_the_catalogue_drops_a_withdrawn_repository(tmp_path, monkeypatch):
    """HACS still lists the repository while its removal is in review; the
    catalogue this project builds from that feed must not."""
    from tools import catalog as catalog_module

    integration_feed = {
        "1311289521": {
            "full_name": "Kaluzaburza/hoymiles-hit-g3-ems",
            "domain": "hoymiles_hit_modbus",
            "last_version": "v1.5.5",
        },
        "55": {"full_name": "a/kept", "domain": "kept", "last_version": "1.0.0"},
    }
    plugin_feed = {"9": {"full_name": "p/card", "last_version": "1.0.0"}}

    def fake_get_json(url, **kwargs):
        return plugin_feed if "/plugin/" in url else integration_feed

    monkeypatch.setattr(catalog_module, "http_get_json", fake_get_json)
    output = tmp_path / "catalog.json"
    assert (
        catalog_main(
            ["--output", str(output), "--excluded", str(_excluded_file(tmp_path))]
        )
        == 0
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    names = [e["full_name"] for e in payload["integrations"]]
    assert names == ["a/kept", "p/card"]
    assert payload["counts"]["total"] == 2
    # The stamp is what tells "HACS delisted it" (the record simply vanishes)
    # from "the owner asked us to stop" (the withdrawal stays visible, with
    # its reason).
    assert payload["excluded"] == [
        {
            "full_name": "Kaluzaburza/hoymiles-hit-g3-ems",
            "domain": "hoymiles_hit_modbus",
            "reason": WITHDRAWN["reason"],
            "reference": WITHDRAWN["reference"],
        }
    ]


def test_apply_exclusions_is_a_noop_with_an_empty_list():
    entries = [{"full_name": "a/one", "domain": "one"}]
    kept, dropped = apply_exclusions(entries, Exclusion([]))
    assert (kept, dropped) == (entries, [])


# --------------------------------------------------------------------------- #
# the crawl's memory
# --------------------------------------------------------------------------- #


def _clean_scan(entry, rules, fetched=None):
    return (
        {
            "domain": entry["domain"],
            "version": entry.get("last_version", ""),
            "ref": "refs/tags/1.0.0",
            "status": "scanned",
            "scanned_utc": "2026-10-13T00:00:00Z",
            "files_scanned": 1,
            "syntax_errors": 0,
            "findings": [],
        },
        [],
    )


def _scan_inputs(tmp_path, *, catalog, findings, state):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {"schema": 1, "core_version": "2026.9", "rules": [SCAN_RULE.to_dict()]}
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"schema": 1, "integrations": catalog}), encoding="utf-8"
    )
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps(findings), encoding="utf-8")
    state_path = tmp_path / "crawl.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return [
        "--rules", str(rules_path),
        "--catalog", str(catalog_path),
        "--findings", str(findings_path),
        "--state", str(state_path),
        "--excluded", str(_excluded_file(tmp_path)),
        "--no-upstream",
    ]


STALE_FINDINGS = {
    "schema": 1,
    "repos": {
        "Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus": {
            "domain": "hoymiles_hit_modbus",
            "status": "scanned",
            "findings": [],
        },
        "Kaluzaburza/hoymiles-hit-g3-ems": {
            "domain": "hoymiles_hit_modbus",
            "status": "scanned",
            "findings": [
                {
                    "rule_id": "device-registry-devices-mapping",
                    "breaks_in": "2027.9",
                    "file": "custom_components/hoymiles_hit_modbus/config_flow.py",
                    "line": 47,
                    "confidence": "high",
                }
            ],
        },
        "a/kept": {"domain": "kept", "status": "scanned", "findings": []},
    },
}

STALE_STATE = {
    "Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus": {
        "last_version_scanned": "v1.5.1",
        "rules_hash": "x",
        "last_scanned_utc": "2026-08-14T04:00:47Z",
    },
    "Kaluzaburza/hoymiles-hit-g3-ems": {
        "last_version_scanned": "v1.5.5",
        "rules_hash": "x",
        "last_scanned_utc": "2026-09-17T01:54:26Z",
    },
}


def test_a_withdrawn_repository_is_purged_under_both_names(tmp_path, monkeypatch):
    """Both records went in under two different names; both have to come out,
    or the survivor keeps the domain on the board."""
    from tools import scan as scan_module
    from tools.common import NotFound

    def refuse(url, **kwargs):
        raise NotFound(url)

    monkeypatch.setattr(scan_module, "scan_repo", _clean_scan)
    monkeypatch.setattr(scan_module, "http_get", refuse)
    argv = _scan_inputs(
        tmp_path,
        catalog=[{"full_name": "a/kept", "domain": "kept", "last_version": "1.0.0"}],
        findings=STALE_FINDINGS,
        state=STALE_STATE,
    )
    assert scan_main(argv) == 0

    findings = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    state = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    assert list(findings["repos"]) == ["a/kept"]
    assert list(state) == ["a/kept"]


def test_a_stale_catalogue_cannot_requeue_a_withdrawn_repository(tmp_path, monkeypatch):
    """catalog.py is the gate, but a catalogue it has not rebuilt since the
    withdrawal must not put the repository back as never-scanned -- the front
    of the queue, and with the repository now private, tomorrow's
    'unreachable' entry on the board."""
    from tools import scan as scan_module

    seen: list[str] = []

    def fake_select_slice(catalog, state, **kwargs):
        seen.extend(entry["full_name"] for entry in catalog)
        return []

    monkeypatch.setattr(scan_module, "select_slice", fake_select_slice)
    argv = _scan_inputs(
        tmp_path,
        catalog=[
            {"full_name": "a/kept", "domain": "kept", "last_version": "1.0.0"},
            {
                "full_name": "Kaluzaburza/hoymiles-hit-g3-ems",
                "domain": "hoymiles_hit_modbus",
                "last_version": "v1.5.5",
            },
        ],
        findings=STALE_FINDINGS,
        state=STALE_STATE,
    )
    assert scan_main(argv) == 0
    assert seen == ["a/kept"], "the withdrawn repository never reaches the queue"


def test_cached_tarballs_for_a_withdrawn_repository_are_deleted(tmp_path):
    """The owner asked for downloaded copies of the source to go too. CI keeps
    no cache, so this is about the maintainer's machine."""
    cache_dir = tmp_path / "tarballs"
    for name in (
        "Kaluzaburza/hoymiles-hit-g3-ems",
        "Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus",
    ):
        cached = cache_path(cache_dir, name, "refs/tags/v1.5.5")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"cached")
    innocent = cache_path(cache_dir, "a/kept", "refs/tags/1.0.0")
    innocent.parent.mkdir(parents=True, exist_ok=True)
    innocent.write_bytes(b"cached")

    purged = purge_cached_tarballs(cache_dir, Exclusion([WITHDRAWN]).names())

    assert purged == 2
    assert not list(cache_dir.glob("*hoymiles*"))
    assert innocent.is_file()


# --------------------------------------------------------------------------- #
# the published index
# --------------------------------------------------------------------------- #


def test_a_stale_findings_record_never_reaches_the_index():
    """scan.py purges on sight; this is the belt to that braces. The findings
    file on disk is the one input build_index cannot trust to have been run."""
    findings = json.loads(json.dumps(STALE_FINDINGS))
    payload = build_payload(
        RULES_DOC, findings, CATALOG_DOC, excluded=Exclusion([WITHDRAWN])
    )

    assert validate_index(payload) == []
    published = {i["full_name"] for i in payload["integrations"]}
    assert "Kaluzaburza/hoymiles-hit-g3-ems" not in published
    assert "hoymiles_hit_modbus" not in payload["releases"].get("2027.9", [])
    assert "hoymiles_hit_modbus" not in payload["clean_domains"]
    assert "hoymiles_hit_modbus" not in payload["unreachable_domains"]


def test_an_excluded_unreachable_domain_stays_out_of_the_index():
    findings = {
        "schema": 1,
        "repos": {
            "Kaluzaburza/hoymiles-hit-g3-ems": {
                "domain": "hoymiles_hit_modbus",
                "status": "unreachable",
                "findings": [],
            }
        },
    }
    payload = build_payload(
        RULES_DOC, findings, CATALOG_DOC, excluded=Exclusion([WITHDRAWN])
    )
    assert "hoymiles_hit_modbus" not in payload["unreachable_domains"]
