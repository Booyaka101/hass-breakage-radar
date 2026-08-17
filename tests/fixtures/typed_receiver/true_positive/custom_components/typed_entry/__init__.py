"""Fixture: every way a DeviceEntry receiver can be proven.

Each ``.config_entries`` read below sits on a receiver the two-pass binder can
prove, so each one is a finding for ``device-entry-config-entries``. The line
numbers are pinned in ``tests/test_typed_receiver.py``.
"""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceRegistry


async def async_remove_config_entry_device(hass, config_entry, device_entry) -> bool:
    """The platform contract: the third parameter is a DeviceEntry."""
    return len(device_entry.config_entries) <= 1


async def prune(hass, entry):
    """The walrus idiom from the deprecation post's own worked example."""
    reg = dr.async_get(hass)
    if device := reg.async_get_device({("x", "y")}):
        for other in device.config_entries:
            hass.config_entries.async_unload(other)
    await hass.config_entries.async_reload(entry.entry_id)


def annotated(device: DeviceEntry, maybe: dr.DeviceEntry | None):
    """Parameter annotations, bare and dotted."""
    a = device.config_entries
    b = maybe.config_entries if maybe else set()
    return a, b


def from_registry_functions(registry: DeviceRegistry, entry_id: str):
    """Module-level helpers returning list[DeviceEntry] bind the for target."""
    for found in dr.async_entries_for_config_entry(registry, entry_id):
        if found.config_entries:
            return found
    created = registry.async_get_or_create(config_entry_id=entry_id)
    return created.config_entries


def chained(hass):
    """No intermediate name at all: the read chains off the lookup."""
    return dr.async_get(hass).async_get_device({("x", "y")}).config_entries
