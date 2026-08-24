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
        "skipped_minified_files": 0,
        "skipped_vendor_files": 0,
        "by_category": {
            "integration": {
                "catalog": 3,
                "scanned": 3,
                "affected": 1,
                "clean": 1,
                "unreachable": 1,
            },
            "plugin": {
                "catalog": 0,
                "scanned": 0,
                "affected": 0,
                "clean": 0,
                "unreachable": 0,
            },
        },
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


def test_html_board_states_the_undetectable_removals(payload):
    payload["coverage"]["rules_published"] = 97
    payload["coverage"]["rules_matchable"] = 41
    board = render_html(payload)
    assert "<b>56</b><span>removals with no detector</span>" in board
    assert "41 of the 97 announced removals" in board
    assert "has not been checked against them" in board


def test_html_board_says_so_when_every_removal_has_a_matcher(payload):
    board = render_html(payload)  # fixture ships 1 published, 1 matchable
    assert "All 1 announced removals tracked here have a matcher" in board


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


# --------------------------------------------------------------------------- #
# release dates on the index and the three-way split on the board (#3)
# --------------------------------------------------------------------------- #

NOW = "2026-08-22T03:39:23Z"


def _docs(repos):
    """One affected repo per ``(name, releases)`` pair, one rule per release."""
    releases = sorted({r for _, rs in repos for r in rs})
    rules = {
        "schema": 1,
        "core_version": "2026.9",
        "rules": [
            {
                "id": f"rule-{release}",
                "kind": "call",
                "symbol": f"symbol_{i}",
                "message": "m",
                "breaks_in": release,
                "source": "https://example.test/rule",
                "origin": "manual",
                "confidence": "high",
                "matchable": True,
            }
            for i, release in enumerate(releases)
        ],
    }
    findings = {
        "schema": 1,
        "repos": {
            name: {
                "domain": name.split("/")[1],
                "version": "1.0.0",
                "status": "scanned",
                "findings": [
                    {
                        "rule_id": f"rule-{release}",
                        "breaks_in": release,
                        "file": "x.py",
                        "line": 1,
                        "confidence": "high",
                    }
                    for release in releases_hit
                ],
            }
            for name, releases_hit in repos
        },
    }
    catalog = {
        "schema": 1,
        "source": "https://example.test/catalog",
        "integrations": [
            {"full_name": name, "domain": name.split("/")[1], "stargazers_count": 1}
            for name, _ in repos
        ],
    }
    return rules, findings, catalog


def _payload(repos, now=NOW):
    return build_payload(*_docs(repos), now=now)


THREE_WAY = [
    ("example/past", ["2026.8"]),
    ("example/near", ["2026.10"]),
    ("example/far", ["2027.8"]),
]


def test_release_dates_are_reproducible_from_generated_utc():
    payload = _payload(THREE_WAY)
    assert payload["generated_utc"] == NOW

    by_name = {i["full_name"]: i for i in payload["integrations"]}
    assert by_name["example/past"]["release_date"] == "2026-08-05"
    assert by_name["example/past"]["days_until"] == -17
    assert by_name["example/near"]["release_date"] == "2026-10-07"
    assert by_name["example/near"]["days_until"] == 46
    assert by_name["example/far"]["release_date"] == "2027-08-04"
    assert by_name["example/far"]["days_until"] == 347

    assert payload["release_dates"] == {
        "2026.8": {"release_date": "2026-08-05", "days_until": -17},
        "2026.10": {"release_date": "2026-10-07", "days_until": 46},
        "2027.8": {"release_date": "2027-08-04", "days_until": 347},
    }


def test_board_splits_into_three_ordered_buckets():
    board = render_html(_payload(THREE_WAY))
    broken = board.index('id="already-broken"')
    soon = board.index('id="within-90-days"')
    later = board.index('id="later"')
    assert broken < soon < later
    # Collapsed by default: no open attribute on the details.
    assert '<details class="bucket" id="later"><summary>Later ' in board
    assert "Later (1 repository)" in board


def test_release_headings_carry_version_date_and_remaining_time():
    board = render_html(_payload(THREE_WAY))
    assert "Home Assistant 2026.10 - 7 October 2026 - in 46 days" in board
    assert "Home Assistant 2026.8 - 5 August 2026 - 17 days ago" in board
    assert "Home Assistant 2027.8 - 4 August 2027 - in 347 days" in board


def test_the_hero_and_later_counts_use_the_earliest_deadline_per_repo():
    """A repo breaking in 2026.10 and again in 2027.8 is one repo breaking
    within 90 days -- summing the per-release table rows would count it twice."""
    board = render_html(
        _payload(
            [
                ("example/both", ["2026.10", "2027.8"]),
                ("example/late", ["2027.8"]),
            ]
        )
    )
    assert "<b>1</b> integration breaks within the next 90 days" in board
    assert "Later (1 repository)" in board
    # The 2027.8 table itself still lists both repos.
    assert '<span class="pill">2 repositories</span>' in board


def test_an_empty_bucket_renders_no_heading():
    board = render_html(_payload([("example/far", ["2027.8"])]))
    assert 'id="already-broken"' not in board
    assert 'id="within-90-days"' not in board
    assert 'id="later"' in board


def test_a_release_exactly_90_days_out_counts_as_within_the_window():
    board = render_html(
        _payload([("example/edge", ["2026.12"])], now="2026-09-03T00:00:00Z")
    )
    assert 'id="within-90-days"' in board
    assert 'id="later"' not in board
    assert "Home Assistant 2026.12 - 2 December 2026 - in 90 days" in board
    assert "<b>1</b> integration breaks within the next 90 days" in board


def test_already_broken_sorts_newest_broken_first():
    board = render_html(
        _payload([("example/old", ["2026.5"]), ("example/new", ["2026.8"])])
    )
    assert board.index("Home Assistant 2026.8") < board.index("Home Assistant 2026.5")


def test_an_unparseable_release_is_listed_with_a_note_not_dropped():
    """Same rule as everywhere else: nothing silently disappears."""
    payload = _payload([("example/odd", ["unknown"]), ("example/near", ["2026.10"])])
    by_name = {i["full_name"]: i for i in payload["integrations"]}
    assert by_name["example/odd"]["release_date"] is None
    assert by_name["example/odd"]["days_until"] is None

    board = render_html(payload)
    assert 'id="unscheduled"' in board
    assert "did not map to a calendar date" in board
    assert "Home Assistant unknown - release date unknown" in board
    # And it never leaks into the dated counts.
    assert "<b>1</b> integration breaks within the next 90 days" in board
