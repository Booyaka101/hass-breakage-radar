"""Fixture: a module-level ``setup_scanner`` in a file that is NOT device_tracker.py.

The legacy device tracker platform API is only the platform API when it lives in
``device_tracker.py``. Here it is just a function with an unlucky name, so this
file must produce ZERO findings.
"""

DOMAIN = "lookalike_tracker"


def setup_scanner(hass, config, see, discovery_info=None):
    """Nothing to do with device_tracker -- wrong file name entirely."""
    return True


async def async_setup_scanner(hass, config, async_see, discovery_info=None):
    """Same again, still the wrong file."""
    return True


def get_scanner(hass, config):
    """And again."""
    return None
