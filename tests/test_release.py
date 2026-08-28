"""Resolving the latest released core version, and the RC-window boundary.

Core's dev branch runs two releases ahead of stable while a release is in RC,
so pending-ness is measured against the newest release PyPI has seen, with the
last known release and then dev minus one behind it (#46).
"""

from __future__ import annotations

import json
import logging

from tools import release
from tools.release import (
    floor_from_payload,
    next_release,
    previous_release,
    release_in_rc,
    resolve_latest_release,
)
from tools.rules_engine import is_pending

DEV = "2026.10"  # the RC window: stable 2026.8, 2026.9 in RC


def _pypi(version, releases=()):
    return lambda url, **kwargs: {
        "info": {"version": version},
        "releases": {v: [] for v in releases},
    }


def _stale(tmp_path, version, ttl_multiple=2, rc=None):
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"version": version, "rc": rc, "fetched_at": 0.0}), encoding="utf-8"
    )
    return cache, release.CACHE_TTL_SECONDS * ttl_multiple


def test_release_arithmetic_crosses_the_year_boundary():
    assert previous_release("2026.10") == "2026.9"
    assert previous_release("2027.1") == "2026.12"
    assert next_release("2026.8") == "2026.9"
    assert next_release("2026.12") == "2027.1"


def test_pypi_version_sets_the_floor_and_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "http_get_json", _pypi("2026.8.3"))
    cache = tmp_path / "cache.json"
    resolved = resolve_latest_release(DEV, cache_path=cache, now=1000.0)
    assert resolved == release.ReleaseFloor("2026.9", "2026.8", "pypi", None)
    assert json.loads(cache.read_text(encoding="utf-8"))["version"] == "2026.8.3"


def test_fresh_cache_answers_without_the_network(tmp_path, monkeypatch):
    def explode(url, **kwargs):
        raise AssertionError("must not fetch while the cache is fresh")

    monkeypatch.setattr(release, "http_get_json", explode)
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"version": "2026.8.3", "rc": "2026.9", "fetched_at": 1000.0}),
        encoding="utf-8",
    )
    resolved = resolve_latest_release(DEV, cache_path=cache, now=1000.0 + 60)
    assert resolved == release.ReleaseFloor("2026.9", "2026.8", "cache", "2026.9")


def test_stale_cache_is_refreshed(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "http_get_json", _pypi("2026.9.1"))
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"version": "2026.8.3", "fetched_at": 1000.0}), encoding="utf-8"
    )
    later = 1000.0 + release.CACHE_TTL_SECONDS + 1
    resolved = resolve_latest_release(DEV, cache_path=cache, now=later)
    assert resolved == release.ReleaseFloor("2026.10", "2026.9", "pypi", None)
    assert json.loads(cache.read_text(encoding="utf-8"))["fetched_at"] == later


def test_no_cache_and_no_network_falls_back_to_dev_minus_one(tmp_path, caplog):
    # conftest points http_get_json at a refusal, so this is the first-run,
    # fully offline case.
    with caplog.at_level(logging.WARNING, logger="breakage_radar.tools"):
        resolved = resolve_latest_release(DEV, cache_path=tmp_path / "cache.json")
    assert resolved == release.ReleaseFloor("2026.9", None, "dev-minus-one", None)
    assert "dev minus one" in caplog.text


def test_a_failed_lookup_reuses_the_last_known_release(tmp_path, caplog):
    """A PyPI blip must not move the floor. Moving it changes rules_hash and
    queues the whole catalogue for a rescan that reverses itself tomorrow."""
    cache, now = _stale(tmp_path, "2026.8.3", rc="2026.9")
    with caplog.at_level(logging.WARNING, logger="breakage_radar.tools"):
        resolved = resolve_latest_release(DEV, cache_path=cache, now=now)
    assert resolved == release.ReleaseFloor("2026.9", "2026.8", "stale-cache", None)
    assert "last known release" in caplog.text
    assert resolved.rc is None, "a remembered RC is not re-asserted while degraded"


def test_the_stale_floor_holds_rules_hash_steady_across_an_outage(tmp_path):
    """The defect this exists for: in a normal cycle the dev-minus-one floor
    is one release lower than the true one, so falling all the way back would
    flip the active rule set on any failed request."""
    cache, now = _stale(tmp_path, "2026.10.1")
    live = resolve_latest_release("2026.11", cache_path=cache, now=0.0)
    outage = resolve_latest_release("2026.11", cache_path=cache, now=now)
    assert live.floor == outage.floor == "2026.11"
    assert (
        resolve_latest_release("2026.11", cache_path=tmp_path / "empty.json").floor
        == "2026.10"
    ), "without a memory the floor really does move"


def test_a_long_dead_cache_never_beats_dev_minus_one(tmp_path):
    # Months old: its floor is lower than dev minus one, so it is discarded
    # rather than over-showing several shipped releases.
    cache, now = _stale(tmp_path, "2026.5.0")
    resolved = resolve_latest_release(DEV, cache_path=cache, now=now)
    assert resolved == release.ReleaseFloor("2026.9", None, "dev-minus-one", None)


def test_offline_reuses_the_last_known_release_too(tmp_path):
    cache, now = _stale(tmp_path, "2026.8.3")
    assert resolve_latest_release(
        DEV, offline=True, cache_path=cache, now=now
    ) == release.ReleaseFloor("2026.9", "2026.8", "stale-cache", None)


def test_offline_flag_skips_the_network_but_not_a_fresh_cache(tmp_path, monkeypatch):
    def explode(url, **kwargs):
        raise AssertionError("offline must not fetch")

    monkeypatch.setattr(release, "http_get_json", explode)
    cache = tmp_path / "cache.json"
    assert resolve_latest_release(
        DEV, offline=True, cache_path=cache
    ) == release.ReleaseFloor("2026.9", None, "dev-minus-one", None)

    cache.write_text(
        json.dumps({"version": "2026.8.3", "fetched_at": 1000.0}), encoding="utf-8"
    )
    assert resolve_latest_release(
        DEV, offline=True, cache_path=cache, now=1000.0
    ) == release.ReleaseFloor("2026.9", "2026.8", "cache", None)


def test_a_prerelease_or_absurd_pypi_version_is_ignored(tmp_path, monkeypatch):
    for bad in ("2026.9.0b3", "2026.9.0rc1", "not-a-version", DEV, "2026.11"):
        monkeypatch.setattr(release, "http_get_json", _pypi(bad))
        resolved = resolve_latest_release(DEV, cache_path=tmp_path / f"{bad}.json")
        assert resolved.source == "dev-minus-one", bad
        assert resolved.floor == "2026.9"


def test_the_floor_crosses_the_year_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "http_get_json", _pypi("2026.12.5"))
    resolved = resolve_latest_release("2027.1", cache_path=tmp_path / "cache.json")
    assert resolved == release.ReleaseFloor("2027.1", "2026.12", "pypi", None)

    resolved = resolve_latest_release(
        "2027.1", offline=True, cache_path=tmp_path / "cache2.json"
    )
    assert resolved == release.ReleaseFloor("2026.12", None, "dev-minus-one", None)


def test_the_release_in_rc_is_named_from_the_prerelease_list():
    """The live shape on the day #46 was fixed: 2026.8.3 is newest, and
    2026.9.0b0/b1 are published, so 2026.9 is in its candidate period."""
    versions = ["2026.8.0b6", "2026.8.2", "2026.8.3", "2026.9.0b0", "2026.9.0b1"]
    assert release_in_rc(versions, "2026.8") == "2026.9"
    # Once 2026.9 ships there is nothing ahead of it until the next branch cut.
    assert release_in_rc(versions, "2026.9") is None
    assert release_in_rc([], "2026.8") is None


def test_the_rc_release_is_resolved_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        release,
        "http_get_json",
        _pypi("2026.8.3", ["2026.8.3", "2026.9.0b0", "2026.9.0b1"]),
    )
    cache = tmp_path / "cache.json"
    resolved = resolve_latest_release(DEV, cache_path=cache, now=1000.0)
    assert resolved == release.ReleaseFloor("2026.9", "2026.8", "pypi", "2026.9")
    assert json.loads(cache.read_text(encoding="utf-8"))["rc"] == "2026.9"


def test_floor_from_payload_reads_the_floor_and_announces_a_degraded_one(caplog):
    assert floor_from_payload(
        {"core_version": DEV, "pending_floor": "2026.9", "pending_floor_source": "pypi"}
    ) == ("2026.9", "pypi")

    with caplog.at_level(logging.WARNING, logger="breakage_radar.tools"):
        assert floor_from_payload(
            {
                "core_version": DEV,
                "pending_floor": "2026.9",
                "pending_floor_source": "stale-cache",
            }
        ) == ("2026.9", "stale-cache")
    assert "last known release" in caplog.text

    # A rules.json written before the floor existed falls back and says so.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="breakage_radar.tools"):
        assert floor_from_payload({"core_version": DEV}) == ("2026.9", "dev-minus-one")
    assert "dev minus one" in caplog.text


def test_pending_boundaries_around_the_rc_release():
    # Stable 2026.8 -> floor 2026.9. The RC release is pending, the shipped
    # one is not, and two releases ahead stays pending.
    floor = "2026.9"
    assert not is_pending("2026.8", floor)
    assert is_pending("2026.9", floor)
    assert is_pending("2026.10", floor)
    # One week later 2026.9 ships, the floor moves, and the rule drops out.
    assert not is_pending("2026.9", "2026.10")
    # Across the year boundary: stable 2026.12, dev 2027.1.
    assert is_pending("2027.1", "2027.1")
    assert not is_pending("2026.12", "2027.1")
