"""The local source scan: what it reads, what it refuses, what it caches.

The shipped ``scan_installed`` running over a user's own ``custom_components``
directory. How its verdict combines with the published index lives in
``test_local_merge.py``.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from conftest import FakeCoordinator, install_component as _install, scan_components as _scan
from custom_components.breakage_radar.report import build_report
from custom_components.breakage_radar.scanner import scan_installed
from custom_components.breakage_radar.sensor import BreakageRadarSensor


# --------------------------------------------------------------------------- #
# the vendored engine
# --------------------------------------------------------------------------- #


def test_vendored_engine_is_byte_identical_to_the_crawlers(repo_root):
    """One engine, two copies -- a drift would let the integration and the
    published index disagree about the same source."""
    crawler = (repo_root / "tools" / "rules_engine.py").read_bytes()
    vendored = (
        repo_root / "custom_components" / "breakage_radar" / "rules_engine.py"
    ).read_bytes()
    assert crawler == vendored


# --------------------------------------------------------------------------- #
# a truncated or unparseable scan never reads as clean
# --------------------------------------------------------------------------- #


def test_unparseable_domain_stays_unknown_with_a_reason(
    tmp_path, index_without_fixture_tracker
):
    broken = tmp_path / "custom_components" / "broken_thing"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("def broken(:\n", encoding="utf-8")

    local = _scan(tmp_path / "custom_components", index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"broken_thing": "1.0"}, local
    )

    assert report["affected_count"] == 0
    assert report["clean_domains"] == []
    assert report["not_in_index"] == ["broken_thing"]
    assert "could not be parsed" in report["not_in_index_reasons"]["broken_thing"]
    assert report["unparsed_files"] == 1


def test_undecodable_bytes_are_counted_not_raised(
    tmp_path, index_without_fixture_tracker
):
    weird = tmp_path / "custom_components" / "weird_thing"
    weird.mkdir(parents=True)
    # Latin-1 decoding always succeeds, so make it a decode-then-parse failure.
    (weird / "__init__.py").write_bytes(b"\xff\xfe\x00\x00 not python \x00")

    local = _scan(tmp_path / "custom_components", index_without_fixture_tracker)
    assert local["domains"]["weird_thing"]["status"] == "unknown"
    assert local["unparsed_files"] == 1


def test_truncated_scan_is_unknown_not_clean(
    tmp_path, index_without_fixture_tracker
):
    big = tmp_path / "custom_components" / "big_thing"
    big.mkdir(parents=True)
    for n in range(3):
        (big / f"module_{n}.py").write_text("VALUE = 1\n", encoding="utf-8")

    local = _scan(
        tmp_path / "custom_components", index_without_fixture_tracker, max_files=1
    )
    result = local["domains"]["big_thing"]
    assert result["status"] == "unknown"
    assert result["skipped_files"] == 2
    assert result["files_scanned"] == 1
    assert "truncated" in result["reason"]

    report = build_report(index_without_fixture_tracker, {"big_thing": ""}, local)
    assert report["clean_domains"] == []
    assert report["skipped_files"] == 2


def test_oversized_file_is_skipped_and_counted(
    tmp_path, index_without_fixture_tracker
):
    fat = tmp_path / "custom_components" / "fat_thing"
    fat.mkdir(parents=True)
    (fat / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (fat / "generated.py").write_text("DATA = 1\n" * 200, encoding="utf-8")

    local = _scan(
        tmp_path / "custom_components", index_without_fixture_tracker, max_bytes=100
    )
    result = local["domains"]["fat_thing"]
    assert result["skipped_files"] == 1
    assert result["files_scanned"] == 1
    assert result["status"] == "unknown"


def test_missing_custom_components_directory_is_an_empty_scan(
    tmp_path, index_without_fixture_tracker
):
    local = _scan(tmp_path / "nope", index_without_fixture_tracker)
    assert local["domains"] == {}
    assert local["files_scanned"] == 0


# --------------------------------------------------------------------------- #
# what the scan never looks at
# --------------------------------------------------------------------------- #


def test_pycache_is_excluded(tmp_path, fixtures_dir, index_without_fixture_tracker):
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    cache_dir = components / "fixture_tracker" / "__pycache__"
    cache_dir.mkdir()
    # A legacy entry point inside __pycache__ must not add a second finding.
    shutil.copy(
        components / "fixture_tracker" / "device_tracker.py",
        cache_dir / "device_tracker.py",
    )
    local = _scan(components, index_without_fixture_tracker)
    assert len(local["domains"]["fixture_tracker"]["findings"]) == 1


def test_breakage_radar_scans_itself_like_anything_else(
    tmp_path, index_without_fixture_tracker
):
    """A tool that exempts itself from its own check is a check that has
    quietly stopped being tested."""
    components = tmp_path / "custom_components"
    ourselves = components / "breakage_radar"
    ourselves.mkdir(parents=True)
    (ourselves / "manifest.json").write_text(
        json.dumps({"domain": "breakage_radar", "version": "1.2.0"}), encoding="utf-8"
    )
    (ourselves / "device_tracker.py").write_text(
        "def setup_scanner(hass, config, see):\n    return True\n", encoding="utf-8"
    )

    local = _scan(components, index_without_fixture_tracker)
    assert local["domains"]["breakage_radar"]["status"] == "affected"

    report = build_report(index_without_fixture_tracker, {"breakage_radar": "1.2.0"}, local)
    assert report["affected_domains"] == ["breakage_radar"]
    assert report["installed_count"] == 1


# --------------------------------------------------------------------------- #
# the scan cache
# --------------------------------------------------------------------------- #


def test_cache_skips_unchanged_domains_and_notices_edits(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    cache: dict = {}

    first = _scan(components, index_without_fixture_tracker, cache=cache)
    assert first["cached_domains"] == 0
    assert first["domains"]["fixture_tracker"]["cached"] is False

    second = _scan(components, index_without_fixture_tracker, cache=cache)
    assert second["cached_domains"] == 1
    assert second["domains"]["fixture_tracker"]["cached"] is True
    assert (
        second["domains"]["fixture_tracker"]["findings"]
        == first["domains"]["fixture_tracker"]["findings"]
    )

    # An edit moves the file's mtime forward: the cache entry must not survive.
    tracker = components / "fixture_tracker" / "device_tracker.py"
    stat = os.stat(tracker)
    os.utime(tracker, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    third = _scan(components, index_without_fixture_tracker, cache=cache)
    assert third["cached_domains"] == 0
    assert third["domains"]["fixture_tracker"]["cached"] is False


def test_cache_is_invalidated_by_a_rules_change(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    cache: dict = {}
    _scan(components, index_without_fixture_tracker, cache=cache)

    index_without_fixture_tracker["rules"][0]["breaks_in"] = "2027.6"
    changed = _scan(components, index_without_fixture_tracker, cache=cache)
    assert changed["cached_domains"] == 0
    assert (
        changed["domains"]["fixture_tracker"]["findings"][0]["breaks_in"] == "2027.6"
    )


# --------------------------------------------------------------------------- #
# the sensor surfaces the scan counters
# --------------------------------------------------------------------------- #


def test_sensor_exposes_the_scan_counters(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    components = _install(tmp_path, fixtures_dir, "true_positive", "fixture_tracker")
    local = _scan(components, index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"fixture_tracker": "0.1.0"}, local
    )
    attributes = BreakageRadarSensor(FakeCoordinator(report)).extra_state_attributes

    assert attributes["files_scanned"] == 1
    assert attributes["unparsed_files"] == 0
    assert attributes["skipped_files"] == 0
    assert attributes["not_analysed"] == []


# --------------------------------------------------------------------------- #
# v1.1.1 regressions: a symlinked component directory is scanned
# --------------------------------------------------------------------------- #


def test_symlinked_component_directory_is_scanned(
    tmp_path, fixtures_dir, index_without_fixture_tracker
):
    """v1.1.0 skipped symlinked component directories (the dev-checkout
    pattern) while discover_installed counted them as installed."""
    components = tmp_path / "custom_components"
    components.mkdir()
    real = tmp_path / "elsewhere" / "fixture_tracker"
    shutil.copytree(
        fixtures_dir / "true_positive" / "custom_components" / "fixture_tracker",
        real,
    )
    try:
        os.symlink(real, components / "fixture_tracker", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this platform")

    local = _scan(components, index_without_fixture_tracker)
    report = build_report(
        index_without_fixture_tracker, {"fixture_tracker": ""}, local
    )
    assert len(local["domains"]["fixture_tracker"]["findings"]) == 1
    assert report["affected_domains"] == ["fixture_tracker"]


# --------------------------------------------------------------------------- #
# the local scan against the live index (opt-in, --run-network)
# --------------------------------------------------------------------------- #


@pytest.mark.network
def test_local_scan_reproduces_the_live_index():
    """Download affected repositories at the exact refs the crawler scanned and
    check the local scan reaches the same findings, line for line. A divergence
    here is a bug in the scan's path handling or merge, not in the index."""
    import io
    import tarfile
    import urllib.request

    def fetch(url):
        request = urllib.request.Request(
            url, headers={"User-Agent": "breakage-radar-tests"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    index = json.loads(
        fetch("https://booyaka101.github.io/hass-breakage-radar/index.json")
    )
    compared = 0
    for entry in index["integrations"]:
        if compared >= 3:
            break
        ref = entry.get("ref") or "refs/heads/main"
        try:
            body = fetch(
                f"https://codeload.github.com/{entry['full_name']}/tar.gz/{ref}"
            )
        except OSError:
            continue  # repository gone or ref rewritten -- not our bug

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    _, _, relative = member.name.partition("/")
                    marker = relative.find("custom_components/")
                    if marker == -1:
                        continue
                    target = os.path.join(root, relative[marker:])
                    if not os.path.realpath(target).startswith(root):
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    handle = archive.extractfile(member)
                    if handle is not None:
                        with open(target, "wb") as out:
                            out.write(handle.read())

            local = scan_installed(
                os.path.join(root, "custom_components"),
                index["rules"],
                current_version=index["core_version"],
            )

        if local["unparsed_files"]:
            # The repository uses syntax newer than this interpreter (the
            # crawler runs 3.14). The merge contract for that case: the domain
            # must come back unknown -- never clean -- so build_report falls
            # back to the index finding.
            assert all(
                d["status"] != "clean" for d in local["domains"].values()
            ), entry["full_name"]
            continue

        expected = sorted(
            (f["rule_id"], f["file"], f["line"]) for f in entry["findings"]
        )
        got = sorted(
            (f["rule_id"], f["file"], f["line"])
            for domain in local["domains"].values()
            for f in domain["findings"]
        )
        assert got == expected, entry["full_name"]
        compared += 1

    assert compared == 3, "fewer than 3 index repositories were fully comparable"
