"""Fixture: every deprecated use of a device registry container.

The worked examples from the 2026-08-24 follow-up post. Line numbers are
pinned in ``test_container_use.py``.
"""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo


async def lookups(hass, device_id):
    """The four mapping uses core's view reports on."""
    reg = dr.async_get(hass)
    entry = reg.devices[device_id]
    found = reg.devices.get(device_id)
    for other in reg.devices.values():
        entry = entry or other
    if device_id in reg.devices:
        return entry, found
    return None


def deleted(hass, device_id):
    """`deleted_devices` is deprecated outright, so any read counts."""
    reg = dr.async_get(hass)
    stale = reg.deleted_devices
    return stale, reg.deleted_devices.get(device_id)


async def register(hass, config_entry):
    """The deprecated device info keywords, on both call forms."""
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("demo", "hub")},
        default_name="Hub",
        default_manufacturer="Acme",
        created_at="2026-01-01T00:00:00Z",
    )
    return DeviceInfo(
        identifiers={("demo", "hub")},
        default_model="X",
        modified_at="2026-01-01T00:00:00Z",
    )


class Pruner:
    """A registry kept on the instance, the shape a coordinator uses."""

    def __init__(self, hass):
        self._registry = dr.async_get(hass)

    def stale(self, entry_id):
        return self._registry.devices.keys()

    def chained(self, hass, device_id):
        return dr.async_get(hass).devices[device_id]
