"""Fixture: metadata that really does set unit_class and mean_type.

Every shape here is one a real integration writes, and none of them is a
finding. The literal shapes are the ones 1.11.0 got wrong -- a keyword matcher
fired on the call, which can never carry ``unit_class=`` at all, so it fired on
correct code too. The rest are mappings this file cannot read in full, where
"not visible here" must not be reported as "missing".
"""

from homeassistant.components.recorder.models import StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
    async_update_statistics_metadata,
)

DOMAIN = "lookalike_statistics"

#: A module-level template, the way one integration keeps it.
TEMPLATE = {
    "has_mean": False,
    "has_sum": True,
    "mean_type": 0,
    "name": "Water",
    "source": DOMAIN,
    "statistic_id": f"{DOMAIN}:water",
    "unit_class": "volume",
    "unit_of_measurement": "m³",
}


def inline_dict(hass, statistics):
    """Both keys written in the literal at the call."""
    async_add_external_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "mean_type": 0,
            "name": "Energy",
            "source": DOMAIN,
            "statistic_id": f"{DOMAIN}:energy",
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        statistics,
    )


def typed_dict_constructor(hass, statistics):
    """The TypedDict's own constructor counts as the literal it is."""
    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=0,
        name="Gas",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:gas",
        unit_class="volume",
        unit_of_measurement="m³",
    )
    async_import_statistics(hass, metadata, statistics)


def local_name(hass, statistics):
    """A local built a few lines above the call."""
    metadata = {
        "has_mean": False,
        "has_sum": True,
        "mean_type": 0,
        "name": "Cost",
        "source": DOMAIN,
        "statistic_id": f"{DOMAIN}:cost",
        "unit_class": None,
        "unit_of_measurement": "EUR",
    }
    async_add_external_statistics(hass, metadata, statistics)


def keys_added_by_subscript(hass, statistics):
    """Written after the literal, but written where the file can see it."""
    metadata = {"has_sum": True, "source": DOMAIN, "statistic_id": f"{DOMAIN}:sub"}
    metadata["mean_type"] = 0
    metadata["unit_class"] = "energy"
    async_import_statistics(hass, metadata, statistics)


def module_level_template(hass, statistics):
    """Resolved out of the module scope."""
    async_add_external_statistics(hass, TEMPLATE, statistics)


def spread_from_elsewhere(hass, statistics, base):
    """``**base`` could carry either key: unreadable, so silent."""
    async_import_statistics(hass, {**base, "name": "Spread"}, statistics)


def built_by_a_helper(hass, statistics):
    """A helper's return value is not readable here."""
    async_add_external_statistics(hass, _build_metadata(), statistics)


def mutated_through_update(hass, statistics):
    """``update()`` can add anything."""
    metadata = {"source": DOMAIN, "statistic_id": f"{DOMAIN}:mutated"}
    metadata.update(_extra())
    async_import_statistics(hass, metadata, statistics)


def handed_in_as_a_parameter(hass, metadata, statistics):
    """The caller owns the mapping."""
    async_add_external_statistics(hass, metadata, statistics)


def computed_key(hass, statistics, field):
    """A computed subscript could be either key."""
    metadata = {"source": DOMAIN, "statistic_id": f"{DOMAIN}:computed"}
    metadata[field] = 0
    async_import_statistics(hass, metadata, statistics)


def rename_only(hass):
    """No new unit, so core's unit_class check is not armed."""
    async_update_statistics_metadata(
        hass, f"{DOMAIN}:water", new_statistic_id=f"{DOMAIN}:water_total"
    )


def new_unit_with_its_class(hass):
    """The new unit and the class that goes with it."""
    async_update_statistics_metadata(
        hass,
        f"{DOMAIN}:water",
        new_unit_of_measurement="L",
        new_unit_class="volume",
    )


def keywords_from_elsewhere(hass, changes):
    """``**changes`` could carry new_unit_class."""
    async_update_statistics_metadata(hass, f"{DOMAIN}:water", **changes)


def _build_metadata():
    return {"source": DOMAIN, "statistic_id": f"{DOMAIN}:helper"}


def _extra():
    return {"mean_type": 0, "unit_class": "energy"}
