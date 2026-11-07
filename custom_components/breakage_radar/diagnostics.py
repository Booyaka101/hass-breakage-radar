"""Downloadable diagnostics: the full report, including every finding.

The sensor carries a compact summary, because Home Assistant's recorder
refuses to store state attributes over 16 KB and a system with many findings
went well past that. Everything is here instead, one click from the
integration page.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BreakageRadarCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the full breakage report for this system."""
    coordinator: BreakageRadarCoordinator = hass.data[DOMAIN][entry.entry_id]
    report = coordinator.data or {}
    scan_errors = getattr(coordinator, "scan_errors", None)
    scan_lock = getattr(coordinator, "_scan_lock", None)

    return {
        "index": {
            "url": coordinator.index_url,
            "generated_utc": report.get("index_generated_utc"),
            "core_version": report.get("index_core_version"),
            "schema": report.get("index_schema"),
        },
        "settings": {
            "alert_window_days": coordinator.alert_window_days,
            "ignored_domains": report.get("ignored_domains"),
        },
        "scan": {
            "enabled": report.get("local_scan_enabled"),
            "files_scanned": report.get("files_scanned"),
            "unparsed_files": report.get("unparsed_files"),
            "skipped_files": report.get("skipped_files"),
            "card_files_scanned": report.get("card_files_scanned"),
            "card_unparsed_files": report.get("card_unparsed_files"),
            "card_skipped_files": report.get("card_skipped_files"),
            "skipped_minified_files": report.get("skipped_minified_files"),
        },
        "coverage": report.get("scan_coverage"),
        "health": {
            "index_update_success": getattr(coordinator, "last_update_success", None),
            "index_error": getattr(coordinator, "last_error", None),
            "scan_errors": dict(scan_errors) if isinstance(scan_errors, dict) else None,
            "local_scan_in_progress": scan_lock.locked() if scan_lock is not None else None,
        },
        "summary": {
            "installed": report.get("installed_count"),
            "installed_versions": report.get("installed_versions"),
            "affected": report.get("affected_count"),
            "broken_now": report.get("broken_now"),
            "imminent": report.get("imminent"),
            "clean": report.get("clean_domains"),
            "not_analysed": report.get("not_in_index"),
            "not_analysed_reasons": report.get("not_in_index_reasons"),
        },
        "cards": {
            "installed": report.get("cards_installed_count"),
            "affected": report.get("affected_cards"),
            "broken_now": report.get("broken_now_cards"),
            "imminent": report.get("imminent_cards"),
            "clean": report.get("clean_cards"),
            "not_analysed": report.get("cards_not_analysed"),
            "not_analysed_reasons": report.get("cards_not_analysed_reasons"),
        },
        "schedule": report.get("schedule"),
        # Every finding, with the message and links the sensor has to leave out.
        "findings": report.get("details"),
        "findings_truncated": report.get("details_truncated"),
        "total_findings": report.get("total_findings"),
    }
