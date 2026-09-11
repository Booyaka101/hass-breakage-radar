"""Fixture: statistics metadata that really is missing the new keys.

Home Assistant 2026.11 requires ``mean_type`` and ``unit_class`` in the
metadata mapping, and ``new_unit_class`` alongside a new unit of measurement.
Every call here omits one of them, in the mapping the file can read in full.
"""

from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
    async_update_statistics_metadata,
)

DOMAIN = "fixture_statistics"


def push_external(hass, statistics):
    """Neither key, inline: two findings on the call."""
    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "name": "Water",
            "source": DOMAIN,
            "statistic_id": f"{DOMAIN}:water",
            "unit_of_measurement": "m³",
        },
        statistics,
    )


def push_internal(hass, statistics):
    """``mean_type`` set, ``unit_class`` forgotten: one finding."""
    metadata = {
        "has_sum": True,
        "mean_type": 0,
        "name": "Energy",
        "source": DOMAIN,
        "statistic_id": "sensor.fixture_energy",
        "unit_of_measurement": "kWh",
    }
    async_import_statistics(hass, metadata, statistics)


def rename_the_unit(hass):
    """A new unit with no unit class: core reports this one."""
    async_update_statistics_metadata(
        hass, "sensor.fixture_energy", new_unit_of_measurement="Wh"
    )
