"""Fixture: a vacuum still overriding the deprecated battery_level property.

Home Assistant 2026.9 removed ``battery_level`` from the base vacuum entity.
Only the class deriving from ``StateVacuumEntity`` is a finding. The
module-level assignment and the unrelated class below share the word and
nothing else.
"""

from homeassistant.components.vacuum import StateVacuumEntity

DOMAIN = "fixture_vacuum"

battery_level = 50


class Foo:
    """Not a vacuum, so its battery_level is nobody's deprecation."""

    battery_level = 50


class MyVacuum(StateVacuumEntity):
    """The shape Home Assistant warned about and then removed."""

    def __init__(self, batt):
        self._batt = batt

    @property
    def battery_level(self):
        return self._batt
