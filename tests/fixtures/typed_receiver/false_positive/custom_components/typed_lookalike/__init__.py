"""Fixture: every ``config_entries`` read that must NOT be a finding.

* ``hass.config_entries`` and ``self.hass.config_entries`` -- the collision the
  matcher exists to dodge.
* ``self.config_entries`` and ``entry.config_entries`` -- unproven receivers.
* ``device`` from the integration's own API client: the receiver is unproven
  AND the call is awaited, so neither pass can bind it.
* ``from .my_registry import async_get`` -- a same-named local helper resolves
  to a relative module, never to the device registry.
* An assignment TO ``.config_entries`` is a Store, not a Load.
* A bare ``DeviceEntry`` annotation with no import proves nothing: it is this
  file's own class of that name.
"""

from .my_registry import async_get


class DeviceEntry:
    """Merely shares a name with the core class."""


class Coordinator:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.config_entries = []

    async def refresh(self):
        for other in self.hass.config_entries.async_entries("demo"):
            await self.hass.config_entries.async_reload(other.entry_id)
        return self.config_entries

    async def lookup(self, device_id):
        device = await self.api.async_get_device(device_id)
        return device.config_entries

    def local_registry(self, hass):
        reg = async_get(hass)
        device = reg.async_get_device_by_identifier(("x", "y"), "abc")
        return device.config_entries


def uses_entry(hass, entry):
    entry.config_entries = ["stored", "not", "read"]
    known = entry.config_entries
    return known, hass.config_entries.async_entries("demo")


def own_class(device: DeviceEntry):
    return device.config_entries
