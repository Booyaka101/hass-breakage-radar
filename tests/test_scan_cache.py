"""The crawler's tarball cache and its download-ahead loop."""

from __future__ import annotations

import pytest

import tools.scan as scan_module
from tools.common import NotFound, RateLimited
from tools.scan import cache_path, fetch_tarball, prefetch


def _http(bodies: dict[str, bytes], calls: list[str]):
    """A fake ``http_get`` that serves ``bodies`` by URL and records each call."""

    def fake_http_get(url, **kwargs):
        calls.append(url)
        if url not in bodies:
            raise NotFound(url)
        return bodies[url]

    return fake_http_get


def test_a_tag_tarball_is_downloaded_once(tmp_path, monkeypatch):
    calls: list[str] = []
    url = scan_module.CODELOAD.format(full_name="a/one", ref="refs/tags/v1.0.0")
    monkeypatch.setattr(scan_module, "http_get", _http({url: b"tarball"}, calls))

    first = fetch_tarball("a/one", "v1.0.0", tmp_path)
    second = fetch_tarball("a/one", "v1.0.0", tmp_path)

    assert first == second == (b"tarball", "refs/tags/v1.0.0")
    assert calls == [url], "the second read must come from disk"
    assert cache_path(tmp_path, "a/one", "refs/tags/v1.0.0").is_file()


def test_a_branch_is_never_cached(tmp_path, monkeypatch):
    """A branch moves, so yesterday's ``main`` is not today's."""
    calls: list[str] = []
    url = scan_module.CODELOAD.format(full_name="a/one", ref="refs/heads/main")
    monkeypatch.setattr(scan_module, "http_get", _http({url: b"tarball"}, calls))

    fetch_tarball("a/one", "", tmp_path)
    fetch_tarball("a/one", "", tmp_path)

    assert calls.count(url) == 2
    assert not list(tmp_path.rglob("*.tar.gz"))


def test_a_new_version_is_a_new_cache_entry(tmp_path):
    assert cache_path(tmp_path, "a/one", "refs/tags/v1.0.0") != cache_path(
        tmp_path, "a/one", "refs/tags/v1.1.0"
    )
    odd = cache_path(tmp_path, "a/one", "refs/tags/1.0.0-beta+build<2>")
    assert odd.parent == tmp_path and "<" not in odd.name


def test_without_a_cache_dir_nothing_is_written(tmp_path, monkeypatch):
    url = scan_module.CODELOAD.format(full_name="a/one", ref="refs/tags/v1.0.0")
    monkeypatch.setattr(scan_module, "http_get", _http({url: b"tarball"}, []))
    assert fetch_tarball("a/one", "v1.0.0") == (b"tarball", "refs/tags/v1.0.0")
    assert not list(tmp_path.rglob("*"))


ENTRIES = [{"full_name": f"a/{n}", "last_version": "v1"} for n in ("one", "two", "three", "four")]


def test_prefetch_keeps_catalogue_order_whatever_finishes_first(monkeypatch):
    import time

    def slow_for_the_first(full_name, last_version, cache_dir):
        if full_name == "a/one":
            time.sleep(0.05)
        return full_name.encode(), "refs/tags/v1"

    monkeypatch.setattr(scan_module, "fetch_tarball", slow_for_the_first)
    got = list(prefetch(ENTRIES, workers=4, cache_dir=None))
    assert [entry["full_name"] for entry, _ in got] == [e["full_name"] for e in ENTRIES]
    assert [fetched for _, fetched in got] == [
        (e["full_name"].encode(), "refs/tags/v1") for e in ENTRIES
    ]


def test_prefetch_hands_over_the_exception_instead_of_raising(monkeypatch):
    """The loop's own error handling in ``scan_repo`` stays the judge."""

    def failing(full_name, last_version, cache_dir):
        if full_name == "a/two":
            raise RateLimited("429")
        return b"", "refs/tags/v1"

    monkeypatch.setattr(scan_module, "fetch_tarball", failing)
    got = list(prefetch(ENTRIES, workers=2, cache_dir=None))
    assert isinstance(got[1][1], RateLimited)
    assert got[0][1] == (b"", "refs/tags/v1")


def test_prefetch_stops_submitting_when_the_consumer_stops(monkeypatch):
    started: list[str] = []

    def record(full_name, last_version, cache_dir):
        started.append(full_name)
        return b"", "refs/tags/v1"

    monkeypatch.setattr(scan_module, "fetch_tarball", record)
    many = [{"full_name": f"a/{n}", "last_version": "v1"} for n in range(50)]
    downloads = prefetch(many, workers=2, cache_dir=None)
    next(downloads)
    downloads.close()
    assert len(started) <= 2 * 2 + 1, "at most the window was ever in flight"


@pytest.mark.parametrize("workers", [1, 3])
def test_scan_repo_judges_a_prefetched_download_like_its_own(workers, monkeypatch):
    from test_scan_cli import CATALOG, RULE, TRACKER_SOURCE, _tarball_bytes

    body = _tarball_bytes({"custom_components/one/device_tracker.py": TRACKER_SOURCE})
    monkeypatch.setattr(
        scan_module, "fetch_tarball", lambda full_name, last_version, cache_dir=None: (body, "refs/tags/1.0.0")
    )
    own, own_findings = scan_module.scan_repo(CATALOG[0], [RULE])
    for entry, fetched in prefetch(CATALOG[:1], workers=workers, cache_dir=None):
        ahead, ahead_findings = scan_module.scan_repo(entry, [RULE], fetched)
    own.pop("scanned_utc"), ahead.pop("scanned_utc")
    assert ahead == own and ahead_findings == own_findings


def test_the_slice_stops_downloading_when_it_ends_early(monkeypatch):
    """A 429 ends the slice. Leaving the pool to drain its window would issue
    the rest of the requests on the way out, which is the opposite of ending
    cleanly."""
    started: list[str] = []

    def record(full_name, last_version, cache_dir):
        started.append(full_name)
        return b"", "refs/tags/v1"

    monkeypatch.setattr(scan_module, "fetch_tarball", record)
    many = [{"full_name": f"a/{n}", "last_version": "v1"} for n in range(60)]
    downloads = prefetch(many, workers=4, cache_dir=None)
    for _ in range(3):
        next(downloads)
    downloads.close()
    assert len(started) < len(many), "closing must cancel what is still queued"
