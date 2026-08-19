"""Index assembly, schema-1 validation and the static board."""

from __future__ import annotations

import copy
import json

import pytest

from tools.build_index import build_payload, main, render_html, validate_index

RULES_DOC = {
    "schema": 1,
    "core_version": "2026.9",
    "core_tarball_sha256": "deadbeef",
    "rules": [
        {
            "id": "legacy-device-tracker-platform",
            "kind": "moduledef",
            "symbol": "setup_scanner",
            "message": "Legacy device tracker platform API.",
            "breaks_in": "2027.5",
            "source": "https://developers.home-assistant.io/blog/2026/04/20/legacy-device-tracker-deprecation/",
            "origin": "manual",
            "confidence": "high",
            "matchable": True,
        },
        {
            "id": "already-gone",
            "kind": "call",
            "symbol": "old_thing",
            "message": "Removed long ago.",
            "breaks_in": "2024.1",
            "source": "homeassistant/x.py:1",
            "origin": "core-ast",
            "confidence": "medium",
            "matchable": True,
        },
    ],
}

FINDINGS_DOC = {
    "schema": 1,
    "repos": {
        "example/affected": {
            "domain": "fixture_tracker",
            "version": "0.1.0",
            "ref": "refs/tags/0.1.0",
            "status": "scanned",
            "scanned_utc": "2026-08-08T08:00:00Z",
            "findings": [
                {
                    "rule_id": "legacy-device-tracker-platform",
                    "breaks_in": "2027.5",
                    "file": "custom_components/fixture_tracker/device_tracker.py",
                    "line": 12,
                    "confidence": "high",
                }
            ],
        },
        "example/clean": {
            "domain": "lookalike_tracker",
            "version": "2.0.0",
            "status": "scanned",
            "findings": [],
        },
        "example/gone": {
            "domain": "gone_away",
            "status": "unreachable",
            "findings": [],
        },
    },
}

CATALOG_DOC = {
    "schema": 1,
    "source": "https://data-v2.hacs.xyz/integration/data.json",
    "integrations": [
        {"full_name": "example/affected", "domain": "fixture_tracker", "stargazers_count": 3},
        {"full_name": "example/clean", "domain": "lookalike_tracker", "stargazers_count": 1},
        {"full_name": "example/gone", "domain": "gone_away", "stargazers_count": 0},
    ],
}


@pytest.fixture
def payload():
    return build_payload(RULES_DOC, FINDINGS_DOC, CATALOG_DOC)


def test_payload_validates_against_schema_1(payload):
    assert payload["schema"] == 1
    assert validate_index(payload) == []


def test_expired_rules_are_not_published(payload):
    assert [rule["id"] for rule in payload["rules"]] == [
        "legacy-device-tracker-platform"
    ]


def test_coverage_counts(payload):
    assert payload["coverage"] == {
        "catalog_total": 3,
        "repos_scanned": 3,
        "repos_delisted": 0,
        "repos_affected": 1,
        "repos_clean": 1,
        "repos_unreachable": 1,
        "findings_total": 1,
        "rules_published": 1,
        "rules_matchable": 1,
    }


def test_clean_and_unreachable_domains_are_listed(payload):
    assert payload["clean_domains"] == ["lookalike_tracker"]
    assert payload["unreachable_domains"] == ["gone_away"]


def test_releases_index(payload):
    assert payload["releases"] == {"2027.5": ["fixture_tracker"]}


def test_rule_hit_rates_are_recorded(payload):
    rule = payload["rules"][0]
    assert rule["hits"] == 1
    assert rule["repos_hit"] == 1


def test_validator_catches_a_dangling_rule_reference(payload):
    payload["integrations"][0]["findings"][0]["rule_id"] = "nope"
    problems = validate_index(payload)
    assert any("unknown rule" in p for p in problems)


def test_validator_catches_a_missing_finding_key(payload):
    del payload["integrations"][0]["findings"][0]["line"]
    assert any("'line'" in p for p in validate_index(payload))


def test_html_board_has_a_non_empty_table(payload):
    html = render_html(payload)
    assert "<table>" in html
    assert "example/affected" in html
    assert "Home Assistant 2027.5" in html
    assert "<tbody><tr" in html
    assert html.count("<tr") >= 2  # header + at least one data row


def test_html_board_handles_an_empty_crawl():
    empty = build_payload(RULES_DOC, {"schema": 1, "repos": {}}, CATALOG_DOC)
    html = render_html(empty)
    assert "No affected integrations" in html


def test_cli_writes_all_three_artifacts(tmp_path):
    rules = tmp_path / "rules.json"
    findings = tmp_path / "findings.json"
    catalog = tmp_path / "catalog.json"
    rules.write_text(json.dumps(RULES_DOC), encoding="utf-8")
    findings.write_text(json.dumps(FINDINGS_DOC), encoding="utf-8")
    catalog.write_text(json.dumps(CATALOG_DOC), encoding="utf-8")
    out = tmp_path / "docs"

    assert (
        main(
            [
                "--rules", str(rules),
                "--findings", str(findings),
                "--catalog", str(catalog),
                "--output-dir", str(out),
            ]
        )
        == 0
    )
    assert (out / "index.json").exists()
    assert (out / "index.html").exists()
    assert (out / ".nojekyll").exists()
    assert json.loads((out / "index.json").read_text(encoding="utf-8"))["schema"] == 1


def test_cli_reports_missing_inputs(tmp_path):
    assert (
        main(
            [
                "--rules", str(tmp_path / "a.json"),
                "--findings", str(tmp_path / "b.json"),
                "--catalog", str(tmp_path / "c.json"),
                "--output-dir", str(tmp_path / "docs"),
            ]
        )
        == 2
    )


def test_published_index_is_valid_and_real(repo_root):
    """Acceptance check 4, asserted against the committed published index."""
    path = repo_root / "docs" / "index.json"
    if not path.exists():
        pytest.skip("docs/index.json not built yet")
    published = json.loads(path.read_text(encoding="utf-8"))
    assert validate_index(published) == []
    assert published["coverage"]["repos_scanned"] > 0
    assert published["catalog_source"].startswith("https://")
    # No placeholder data may ship: every listed repo must be a real slug.
    for integration in published["integrations"]:
        assert "/" in integration["full_name"]
        assert not integration["full_name"].startswith("example/")


def test_coverage_never_claims_more_scanned_than_the_catalogue_holds():
    """A repository that is renamed or delisted keeps its findings record, so
    counting the findings file made repos_scanned exceed catalog_total.

    Seen live: drake69/NeverDry became never-dry/NeverDry, and the crawl
    reported 3089 scanned against a 3088 repository catalogue.
    """
    from tools.build_index import build_payload

    rules_doc = {"core_version": "2026.9", "rules": []}
    catalog_doc = {"integrations": [{"full_name": "owner/still-listed", "domain": "a"}]}
    findings_doc = {
        "repos": {
            "owner/still-listed": {"status": "scanned", "domain": "a", "findings": []},
            "owner/renamed-away": {"status": "scanned", "domain": "b", "findings": []},
        }
    }

    coverage = build_payload(rules_doc, findings_doc, catalog_doc)["coverage"]
    assert coverage["repos_scanned"] == 1
    assert coverage["repos_delisted"] == 1
    assert coverage["repos_scanned"] <= coverage["catalog_total"]


def test_confidence_comes_from_the_rule_not_the_crawl_record():
    """A re-rating has to reach the board without a full rescan.

    ``rules_hash`` deliberately ignores confidence, so raising or lowering a
    rule's rating never invalidates a cached scan and the stored findings keep
    whatever they were rated when that repository was last visited.
    """
    rules = copy.deepcopy(RULES_DOC)
    findings = copy.deepcopy(FINDINGS_DOC)
    findings["repos"]["example/affected"]["findings"][0]["confidence"] = "medium"

    payload = build_payload(rules, findings, CATALOG_DOC)
    assert payload["integrations"][0]["findings"][0]["confidence"] == "high"


def test_a_finding_whose_rule_vanished_keeps_its_own_confidence():
    rules = copy.deepcopy(RULES_DOC)
    rules["rules"] = [r for r in rules["rules"] if r["id"] != "legacy-device-tracker-platform"]
    payload = build_payload(rules, copy.deepcopy(FINDINGS_DOC), CATALOG_DOC)

    assert payload["integrations"][0]["findings"][0]["confidence"] == "high"
