"""Catalogue normalisation and the data-v2 -> hacs/default fallback."""

from __future__ import annotations

import json

import pytest

from tools import catalog as catalog_module
from tools.catalog import (
    fetch_catalog,
    main,
    normalise_fallback,
    normalise_primary,
)

PRIMARY_SAMPLE = {
    "308963330": {
        "full_name": "dave-code-ruiz/elkbledom",
        "domain": "elkbledom",
        "last_version": "1.6.5",
        "stargazers_count": 198,
        "open_issues": 7,
        "last_updated": "2026-08-07T09:12:39Z",
        "manifest": {"name": "elkbledom"},
    },
    "417802358": {
        "full_name": "Chouffy/home_assistant_tgtg",
        "domain": "tgtg",
        "last_version": "1.4.0",
        "stargazers_count": 100,
        "open_issues": 2,
        "last_updated": "2026-05-01T00:00:00Z",
    },
    "999": {"not_a_repo": True},
    "1000": "garbage",
}

FALLBACK_SAMPLE = [
    "007hacky007/car_maintenance",
    "0jety0/emaux_spv150",
    "0xAHA/airtouch4_advanced",
    "not-a-slug",
    42,
]


def test_normalise_primary_keeps_the_verified_shape():
    entries = normalise_primary(PRIMARY_SAMPLE)
    assert len(entries) == 2
    elk = next(e for e in entries if e["full_name"] == "dave-code-ruiz/elkbledom")
    assert elk == {
        "full_name": "dave-code-ruiz/elkbledom",
        "domain": "elkbledom",
        "last_version": "1.6.5",
        "stargazers_count": 198,
        "open_issues": 7,
        "last_updated": "2026-08-07T09:12:39Z",
        "repo_id": "308963330",
    }


def test_normalise_fallback_drops_non_slugs():
    entries = normalise_fallback(FALLBACK_SAMPLE)
    assert [e["full_name"] for e in entries] == [
        "007hacky007/car_maintenance",
        "0jety0/emaux_spv150",
        "0xAHA/airtouch4_advanced",
    ]
    assert all(e["domain"] == "" and e["last_version"] == "" for e in entries)


def test_normalise_rejects_the_wrong_container_type():
    with pytest.raises(ValueError):
        normalise_primary([1, 2, 3])
    with pytest.raises(ValueError):
        normalise_fallback({"a": 1})


def test_fallback_is_used_when_the_primary_source_fails(monkeypatch):
    calls: list[str] = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        if "data-v2" in url:
            raise RuntimeError("503 Service Unavailable")
        return FALLBACK_SAMPLE

    monkeypatch.setattr(catalog_module, "http_get_json", fake_get_json)
    entries, source = fetch_catalog()
    assert source.endswith("/hacs/default/master/integration")
    assert len(entries) == 3
    assert len(calls) == 2


def test_fallback_is_used_when_the_primary_source_is_empty(monkeypatch):
    def fake_get_json(url, **kwargs):
        return {} if "data-v2" in url else FALLBACK_SAMPLE

    monkeypatch.setattr(catalog_module, "http_get_json", fake_get_json)
    entries, source = fetch_catalog()
    assert source.endswith("/hacs/default/master/integration")
    assert len(entries) == 3


def test_both_sources_failing_raises_a_clear_error(monkeypatch):
    def fake_get_json(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(catalog_module, "http_get_json", fake_get_json)
    with pytest.raises(RuntimeError, match="network down"):
        fetch_catalog()


def test_cli_reports_a_failure_instead_of_crashing(monkeypatch, tmp_path):
    def fake_get_json(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(catalog_module, "http_get_json", fake_get_json)
    assert main(["--output", str(tmp_path / "catalog.json")]) == 1


def test_cli_writes_a_sorted_catalogue(monkeypatch, tmp_path):
    monkeypatch.setattr(
        catalog_module, "http_get_json", lambda url, **kwargs: PRIMARY_SAMPLE
    )
    output = tmp_path / "catalog.json"
    assert main(["--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["counts"]["total"] == 2
    names = [e["full_name"] for e in payload["integrations"]]
    assert names == sorted(names, key=str.lower)


def test_shipped_catalogue_has_more_than_100_entries(repo_root):
    """Acceptance check 2, asserted against the committed real fetch."""
    path = repo_root / "data" / "catalog.json"
    if not path.exists():
        pytest.skip("data/catalog.json not built yet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["counts"]["total"] > 100
    assert payload["source"].startswith("https://")
