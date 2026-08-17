"""Fixture: every ``config_entries`` read that must NOT be a finding.

The whole point of the typed matcher is that ``config_entries`` is also the
ubiquitous ``hass.config_entries``. Everything here must scan clean under the
full shipped rule set, not just this rule.
"""

from .my_registry import async_get


class DeviceEntry:
    """Merely shares a name with the core class."""


class Coordinator:
    def __init__(self, hass, entry, api):
        self.hass = hass
        self.entry = entry
        self.api = api
        self.config_entries = []
        self._registry = api.registry

    async def refresh(self):
        for other in self.hass.config_entries.async_entries("demo"):
            await self.hass.config_entries.async_reload(other.entry_id)
        return self.config_entries

    async def lookup(self, device_id):
        """Their own client's method of the same name, and awaited."""
        device = await self.api.async_get_device(device_id)
        return device.config_entries

    def own_registry(self, hass):
        """``async_get`` here is the relative import above, not the real one."""
        device = async_get(hass).async_get_device_by_identifier(("x", "y"), "abc")
        return device.config_entries

    def registry_from_the_api(self, device_id):
        """``self._registry`` was never assigned from the device registry."""
        return self._registry.async_get(device_id).config_entries


class Elsewhere:
    """A sibling class proves nothing about this one."""

    def go(self, device_id):
        return self._registry.async_get(device_id).config_entries

    async def async_remove_config_entry_device(self, hass, entry, device_entry):
        """A method, not the module-level platform contract."""
        return device_entry.config_entries


def async_remove_config_entry_device(hass, entry, device_entry):
    """Home Assistant only ever calls the async form."""
    return device_entry.config_entries


def uses_entry(hass, entry):
    entry.config_entries = ["stored", "not", "read"]
    return entry.config_entries, hass.config_entries.async_entries("demo")


def own_class(device: DeviceEntry):
    """This file's own DeviceEntry, not the registry's."""
    return device.config_entries


def from_a_local_helper(reg, entry_id):
    """A same-named helper of our own returns whatever it likes."""
    from .my_registry import async_entries_for_config_entry

    for device in async_entries_for_config_entry(reg, entry_id):
        return device.config_entries
    return None
