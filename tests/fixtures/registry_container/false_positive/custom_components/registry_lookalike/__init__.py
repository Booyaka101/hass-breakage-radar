"""Fixture: every ``devices`` read that must NOT be a finding.

Iterating a registry container is the supported use and stays supported, and
``devices`` is a field on half the coordinators in this ecosystem. Everything
here must scan clean under the full shipped rule set, not just one rule.
"""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .registry import async_get

#: A module-level mapping of our own that happens to share the name.
devices = {}


def supported(hass):
    """Iteration, length and listing all keep working past 2027.9."""
    reg = dr.async_get(hass)
    for device in reg.devices:
        print(device)
    known = list(reg.devices)
    return len(reg.devices), known


def value_membership(hass, device_entry):
    """`DeviceEntry in registry.devices` is the supported membership test."""
    reg = dr.async_get(hass)
    return device_entry in reg.devices


def handed_on(hass, sink):
    """Passing the view around is not a mapping use."""
    reg = dr.async_get(hass)
    return sink(reg.devices)


def our_own_mapping(key):
    return devices[key]


class Coordinator:
    def __init__(self, hass, api):
        self.hass = hass
        self.api = api
        self.devices = {}
        self.deleted_devices = {}

    def lookup(self, key):
        """Our own container, not the registry's."""
        return self.coordinator.devices[key], self.devices.get(key)

    def deleted(self, key):
        return self.deleted_devices[key]


def own_registry(hass, device_id):
    """``async_get`` here is the relative import above, not the real one."""
    return async_get(hass).devices[device_id]


def healthy_device_info():
    return DeviceInfo(name="X", model="Y", manufacturer="Z")
