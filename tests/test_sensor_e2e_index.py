"""The shipped sensor logic, run against the index this repository publishes.

Not a unit test with a fixture: it loads ``docs/index.json`` as built by the
real crawl and asks the released ``report.py`` what a box running the first few
affected integrations would be told.
"""

from __future__ import annotations

import json

import pytest

from custom_components.breakage_radar.report import build_report, validate_index


@pytest.fixture(scope="module")
def published_index(repo_root):
    path = repo_root / "docs" / "index.json"
    if not path.exists():
        pytest.skip("docs/index.json not built yet -- run tools/build_index.py")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_published_index_drives_the_shipped_sensor(published_index):
    assert validate_index(published_index) is None

    installed = {
        entry["domain"]: entry.get("version", "0")
        for entry in published_index["integrations"][:3]
        if entry.get("domain")
    }
    assert installed, "the published index lists no affected integration"

    report = build_report(published_index, installed, current_version="2026.9")
    assert report["installed_count"] == len(installed)
    assert report["affected_count"] == len(installed)
    assert sorted(report["affected_domains"]) == sorted(installed)
    assert report["total_findings"] >= len(installed)
    assert report["schedule"], "an affected box must get a dated schedule"
