"""Fetches the published breakage index and matches it against this system."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from homeassistant import const as ha_const
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, FETCH_TIMEOUT, INDEX_URL, UPDATE_INTERVAL
from .report import build_report, discover_installed, scan_installed, validate_index

_LOGGER = logging.getLogger(__name__)


class BreakageRadarCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps the local breakage report up to date.

    On a fetch failure the last good report is kept in ``self.data`` and
    ``last_update_success`` goes false, which makes the sensor unavailable
    instead of raising or reporting stale data as fresh.
    """

    def __init__(self, hass: HomeAssistant, index_url: str = INDEX_URL) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.index_url = index_url
        self.last_error: str | None = None
        self._index: dict[str, Any] | None = None
        #: ``domain -> (signature, result)``; lets the 12-hourly refresh skip
        #: re-parsing any integration whose files and rules have not changed.
        self._scan_cache: dict[str, Any] = {}

    async def _fetch_index(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                self.index_url,
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT),
                headers={"Accept": "application/json"},
            ) as response:
                if response.status == 429:
                    raise UpdateFailed(
                        "Breakage Radar index is rate limited (HTTP 429); "
                        "keeping the previous report"
                    )
                if response.status != 200:
                    raise UpdateFailed(
                        f"Breakage Radar index returned HTTP {response.status} "
                        f"for {self.index_url}"
                    )
                body = await response.text()
        except asyncio.TimeoutError as err:
            raise UpdateFailed(
                f"Timed out after {FETCH_TIMEOUT}s fetching {self.index_url}"
            ) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                f"Could not reach {self.index_url}: {err}"
            ) from err

        try:
            payload = json.loads(body)
        except ValueError as err:
            raise UpdateFailed(f"Index at {self.index_url} is not valid JSON: {err}") from err

        problem = validate_index(payload)
        if problem:
            raise UpdateFailed(f"Index at {self.index_url} is unusable: {problem}")
        return payload

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            index = await self._fetch_index()
        except UpdateFailed as err:
            self.last_error = str(err)
            if self._index is None:
                # Nothing cached yet -- there is genuinely no report to give.
                raise
            _LOGGER.warning(
                "%s; reusing the index fetched earlier and marking the sensor "
                "unavailable",
                err,
            )
            raise

        self.last_error = None
        self._index = index

        components_dir = self.hass.config.path("custom_components")
        current_version = getattr(ha_const, "__version__", "") or index.get(
            "core_version", ""
        )
        installed = await self.hass.async_add_executor_job(
            discover_installed, components_dir
        )
        local_scan = await self.hass.async_add_executor_job(
            self._scan_local, components_dir, index, current_version
        )
        report = build_report(
            index, installed, local_scan, current_version=current_version
        )
        _LOGGER.debug(
            "Breakage Radar: %d of %d custom integrations affected, %d broken now "
            "(%d file(s) scanned locally, %d cached)",
            report["affected_count"],
            report["installed_count"],
            report["broken_now_count"],
            report["files_scanned"],
            (local_scan or {}).get("cached_domains", 0),
        )
        return report

    def _scan_local(
        self, components_dir: str, index: dict[str, Any], current_version: str
    ) -> dict[str, Any] | None:
        """Run the local source scan. Blocking -- called from the executor.

        The scan itself swallows per-file problems; anything unexpected beyond
        that degrades to index-only matching rather than losing the report.
        """
        try:
            return scan_installed(
                components_dir,
                index.get("rules", []),
                current_version=current_version,
                cache=self._scan_cache,
            )
        except Exception:  # noqa: BLE001 - a scan bug must never kill the sensor
            _LOGGER.exception(
                "Local source scan failed; falling back to index-only matching"
            )
            return None
