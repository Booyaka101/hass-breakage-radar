"""Fixture: code that *looks* legacy but is not.

Everything here must produce ZERO findings:

* ``setup_scanner`` defined inside a class body is a method, not the module-level
  platform entry point Home Assistant looks for.
* ``DeviceScanner`` used as a plain class *name* (not a base class) is somebody
  else's helper, not ``homeassistant.components.device_tracker.DeviceScanner``.
* ``battery_level`` on a class that does not derive from a device tracker base
  class is an ordinary property.
* ``async_get_device`` resolved to a module that is not the device registry.
"""

from homeassistant.components.device_tracker import ScannerEntity

from .my_own_registry import async_get_device

DOMAIN = "lookalike_tracker"


class DeviceScanner:
    """A helper that merely shares a name with the deprecated core class."""

    def setup_scanner(self, hass, config, see, discovery_info=None):
        """A method, not a module-level platform entry point."""
        return True

    async def async_setup_scanner(self, hass, config, async_see, discovery_info=None):
        """Also a method."""
        return True


class PlainThing:
    """Not a tracker entity, so its battery_level is nobody's deprecation."""

    @property
    def battery_level(self) -> int:
        return 42

    @property
    def location_name(self) -> str:
        return "home"


class ModernScanner(ScannerEntity):
    """The migrated shape: no legacy entry point, no deprecated properties."""

    @property
    def is_connected(self) -> bool:
        return True

    def lookup(self, identifier):
        """Calls a same-named helper from our own module, not the registry."""
        return async_get_device(identifier)
