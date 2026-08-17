"""Fixture: every way a DeviceEntry receiver can be proved.

Each ``.config_entries`` read below sits on a receiver the binder can prove, so
each one is a finding. Line numbers are pinned in ``test_typed_receiver.py``.
"""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceRegistry


async def async_remove_config_entry_device(hass, config_entry, device_entry) -> bool:
    """The platform contract: the third parameter is a DeviceEntry."""
    return len(device_entry.config_entries) <= 1


async def prune(hass, entry):
    """The walrus idiom from the deprecation post's own example."""
    reg = dr.async_get(hass)
    if device := reg.async_get_device({("x", "y")}):
        for other in device.config_entries:
            hass.config_entries.async_unload(other)
    await hass.config_entries.async_reload(entry.entry_id)


def annotated(device: DeviceEntry, maybe: dr.DeviceEntry | None, named: "DeviceEntry"):
    """Parameter annotations: bare, dotted, and as a string."""
    return device.config_entries, maybe.config_entries, named.config_entries


def from_registry_functions(registry: DeviceRegistry, entry_id: str):
    """Module-level helpers return list[DeviceEntry], in both loop forms."""
    for found in dr.async_entries_for_config_entry(registry, entry_id):
        if found.config_entries:
            return found
    return any(
        entry_id in device.config_entries
        for device in dr.async_entries_for_area(registry, "kitchen")
    )


def chained(hass):
    """No intermediate name at all: the read chains off the lookup."""
    return dr.async_get(hass).async_get_device({("x", "y")}).config_entries


def closure(hass):
    """A nested function really does read the enclosing name."""
    device = dr.async_get(hass).async_get_device({("x", "y")})

    def inner():
        return device.config_entries

    return inner


class Searcher:
    """A registry kept on the instance, the shape core's own search uses."""

    def __init__(self, hass):
        self._device_registry = dr.async_get(hass)

    def entries_for(self, device_id):
        if device_entry := self._device_registry.async_get(device_id):
            return device_entry.config_entries
        return set()


def from_the_registry_mapping(hass, device_id):
    """The registry's own mappings hold entries too."""
    reg = dr.async_get(hass)
    for device in reg.devices.values():
        if device.config_entries:
            return device
    deleted = reg.deleted_devices[device_id]
    return deleted.config_entries, dr.async_get(hass).devices.get(device_id).config_entries
