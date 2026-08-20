"""The coordinator's update flow, issue #1 in particular.

Setup waits on the first update, so the first update must never wait on the
local scan. These drive the real BreakageRadarCoordinator with a fake hass
that records what was scheduled where.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from custom_components.breakage_radar.coordinator import BreakageRadarCoordinator


class FakeConfig:
    def __init__(self, components_dir):
        self._dir = str(components_dir)

    def path(self, *parts):
        # The real hass.config.path resolves relative to the config dir; here
        # only custom_components exists, so www/community resolves to a path
        # that does not, the way a box with no cards installed looks.
        if parts and parts[0] == "custom_components":
            return self._dir
        return self._dir + "-" + "-".join(p.replace("/", "-") for p in parts)


class FakeHass:
    """Just enough hass: an executor and a background task queue."""

    def __init__(self, components_dir):
        self.config = FakeConfig(components_dir)
        self.background_tasks = []

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def async_create_background_task(self, coro, name=None):
        self.background_tasks.append(coro)
        return coro


@pytest.fixture
def coordinator(tmp_path, fixtures_dir, sample_index):
    """A coordinator over a tmp custom_components with the true-positive
    fixture installed, index fetch stubbed out."""
    components = tmp_path / "custom_components"
    source = fixtures_dir / "true_positive" / "custom_components" / "fixture_tracker"
    shutil.copytree(source, components / "fixture_tracker")
    (components / "fixture_tracker" / "manifest.json").write_text(
        json.dumps({"domain": "fixture_tracker", "version": "0.1.0"}),
        encoding="utf-8",
    )

    coordinator = BreakageRadarCoordinator(FakeHass(components))

    async def fake_fetch():
        return sample_index

    coordinator._fetch_index = fake_fetch
    return coordinator


def test_first_update_does_not_wait_for_the_scan(coordinator, monkeypatch):
    """The bug behind issue #1: the scan ran inside the first update, so
    config entry setup sat waiting on it and got cancelled."""
    scanned = []
    monkeypatch.setattr(
        coordinator,
        "_scan_local",
        lambda *args: scanned.append(args) or None,
    )

    report = asyncio.run(coordinator._async_update_data())

    # The update finished without the scan having run...
    assert scanned == []
    # ...but scheduled it in the background,
    assert len(coordinator.hass.background_tasks) == 1
    # and still produced a usable index-based report meanwhile.
    assert report["affected_domains"] == ["fixture_tracker"]
    assert report["details"][0]["source"] == "index"
    assert report["local_scan_enabled"] is False
    for task in coordinator.hass.background_tasks:
        task.close()


def test_background_scan_publishes_local_results(coordinator):
    async def run():
        await coordinator._async_update_data()
        for task in coordinator.hass.background_tasks:
            await task

    asyncio.run(run())

    report = coordinator.data
    assert report["local_scan_enabled"] is True
    assert report["details"][0]["source"] == "local"
    assert report["details"][0]["line"] == 12
    assert report["files_scanned"] == 1


def test_a_failed_scan_keeps_the_index_results(coordinator, monkeypatch):
    monkeypatch.setattr(coordinator, "_scan_local", lambda *args: None)

    async def run():
        report = await coordinator._async_update_data()
        for task in coordinator.hass.background_tasks:
            await task
        return report

    report = asyncio.run(run())
    assert report["affected_domains"] == ["fixture_tracker"]
    assert report["details"][0]["source"] == "index"
    # The failed scan must not have published anything worse.
    assert coordinator.data is None or coordinator.data == report


def test_scan_before_any_index_is_a_no_op(coordinator):
    asyncio.run(coordinator.async_run_local_scan())
    assert coordinator.data is None


def test_next_refresh_reuses_the_last_scan_without_waiting(coordinator, monkeypatch):
    async def run():
        await coordinator._async_update_data()
        for task in coordinator.hass.background_tasks:
            await task
        coordinator.hass.background_tasks.clear()

        # Second refresh: the scan from last time is used immediately, and a
        # new background scan is scheduled rather than awaited.
        monkeypatch.setattr(
            coordinator, "_scan_local", lambda *args: pytest.fail("waited on scan")
        )
        report = await coordinator._async_update_data()
        for task in coordinator.hass.background_tasks:
            task.close()
        return report

    report = asyncio.run(run())
    assert report["details"][0]["source"] == "local"
    assert report["local_scan_enabled"] is True
