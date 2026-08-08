"""Fixture: a custom integration still on the legacy device tracker platform API.

A module-level ``setup_scanner`` in a file named ``device_tracker.py`` is the
legacy platform entry point Home Assistant removes in the 2027.5 release.
"""

from homeassistant.const import CONF_HOST

DOMAIN = "fixture_tracker"


def setup_scanner(hass, config, see, discovery_info=None):
    """Set up the legacy scanner. Removed in Home Assistant 2027.5."""
    see(dev_id="fixture", host_name=config.get(CONF_HOST))
    return True
