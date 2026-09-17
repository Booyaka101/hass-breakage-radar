"""`modbus.get_hub`, deprecated 2026.10 and removed 2027.10.

Not a one-to-one swap: `async_get_unit` builds a connection from a host, port
and unit id the caller's own config flow collects, instead of attaching to a
hub the user named in YAML. So the rule has to be certain before it tells
someone to do that work.
"""

from __future__ import annotations

import pytest

from tools.rules_engine import match_source

#: wills106/homeassistant-solax-modbus, modbus_transport.py, verbatim. The
#: import is inside the function because Core Modbus is optional there.
REAL_CALL_SITE = (
    "from homeassistant.core import HomeAssistant\n"
    "\n"
    "def _get_core_hub(hass: HomeAssistant, name: str):\n"
    "    try:\n"
    "        from homeassistant.components.modbus import get_hub\n"
    "    except ImportError:\n"
    "        return None\n"
    "    try:\n"
    "        return get_hub(hass, name)\n"
    "    except KeyError:\n"
    "        return None\n"
)


@pytest.fixture(scope="module")
def rule(shipped_matchable_rules):
    return [r for r in shipped_matchable_rules if r.id == "modbus-get-hub"]


def test_the_real_call_site_is_found_once(rule):
    (finding,) = match_source("custom_components/x/modbus_transport.py", REAL_CALL_SITE, rule)
    assert finding.rule_id == "modbus-get-hub"
    assert finding.line == 9
    assert finding.breaks_in == "2027.10"


def test_the_module_qualified_call_is_found_too(rule):
    source = (
        "from homeassistant.components import modbus\n"
        "\n"
        "def go(hass):\n"
        "    return modbus.get_hub(hass, 'solax')\n"
    )
    assert [f.line for f in match_source("custom_components/x/__init__.py", source, rule)] == [4]


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "someone else's get_hub",
            "from .helpers import get_hub\n"
            "\n"
            "def go(hass):\n"
            "    return get_hub(hass, 'solax')\n",
        ),
        (
            "an attribute nobody proved is modbus",
            "def go(hass, domain):\n    return hass.data[domain].get_hub('solax')\n",
        ),
        (
            "the replacement",
            "from homeassistant.components.modbus import async_get_unit\n"
            "\n"
            "async def async_setup_entry(hass, entry, params):\n"
            "    return async_get_unit(hass, entry, params, entry.data['unit_id'])\n",
        ),
        ("the name in a comment", "# get_hub is gone in 2027.10\n"),
    ],
)
def test_nothing_else_is_flagged(label, source, rule):
    assert match_source("custom_components/x/__init__.py", source, rule) == [], label
