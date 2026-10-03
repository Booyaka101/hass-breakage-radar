"""Breakage Radar for Home Assistant.

Tells you which of your installed custom integrations use Home Assistant APIs
that are already scheduled for removal, and in which release they go away.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    ALERT_WINDOW_DAYS,
    CONF_ALERT_WINDOW_DAYS,
    CONF_IGNORED_DOMAINS,
    DOMAIN,
)
from .coordinator import BreakageRadarCoordinator
from .repairs import async_sync_issue

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Breakage Radar from a config entry."""
    coordinator = BreakageRadarCoordinator(
        hass,
        alert_window_days=entry.options.get(
            CONF_ALERT_WINDOW_DAYS, ALERT_WINDOW_DAYS
        ),
        ignored_domains=entry.options.get(CONF_IGNORED_DOMAINS) or (),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    @callback
    def _handle_update() -> None:
        """Keep the repairs issues in step with every refresh."""
        if coordinator.last_update_success and coordinator.data:
            async_sync_issue(hass, coordinator.data)

    entry.async_on_unload(coordinator.async_add_listener(_handle_update))
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    if coordinator.data:
        async_sync_issue(hass, coordinator.data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options without needing a restart."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    The repairs issues are left in place: Home Assistant keeps a user's
    Ignore as the dismissal version on the issue registry entry, and deleting
    the issue deletes that with it. A non-persistent issue that stops being
    recreated leaves the panel on its own after a restart.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.data.pop(DOMAIN, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the repairs issues when the config entry itself is removed."""
    registry = ir.async_get(hass)
    for issue_domain, issue_id in list(registry.issues):
        if issue_domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
