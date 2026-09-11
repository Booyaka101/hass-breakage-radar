"""The 2026.11 statistics metadata rules.

``unit_class`` and ``mean_type`` are keys of the ``metadata`` mapping, not
keywords, so ``async_import_statistics(hass, metadata, stats)`` can never pass
``unit_class=`` at all. 1.11.0 read core's prose as a keyword and shipped 99
wrong findings over 96 integrations; two maintainers reported it
(ReikanYsora/Helios-Forecast#38,
barisdemirdelen/homeassistant-greenchoice#66). The matcher now reads the
mapping, and the extractor reads core's ``if`` rather than its prose.
"""

from __future__ import annotations

import pytest
from conftest import scan_fixture_tree

from tools.extract_rules import build_rules, extract_from_source
from tools.rules_engine import load_rules, match_source

#: The real guards, copied from home-assistant/core ``dev`` at 2026.9. Note
#: the ``mean_type`` marker inside ``async_add_external_statistics``: its prose
#: names ``async_import_statistics``, which is core's own copy-paste and is why
#: a prose-derived rule folded the two functions into one.
CORE_STATISTICS = b'''
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.frame import report_usage
from homeassistant.helpers.typing import UNDEFINED, UndefinedType


@callback
def async_update_statistics_metadata(
    hass: HomeAssistant,
    statistic_id: str,
    *,
    new_statistic_id: str | UndefinedType = UNDEFINED,
    new_unit_class: str | UndefinedType | None = UNDEFINED,
    new_unit_of_measurement: str | UndefinedType | None = UNDEFINED,
    _called_from_ws_api: bool = False,
) -> None:
    """Update statistics metadata for a statistic_id."""
    if new_unit_of_measurement is not UNDEFINED and new_unit_class is UNDEFINED:
        if not _called_from_ws_api:
            report_usage(
                (
                    "doesn't specify unit_class when calling "
                    "async_update_statistics_metadata"
                ),
                breaks_in_ha_version="2026.11",
                exclude_integrations={DOMAIN},
            )


@callback
def async_import_statistics(
    hass: HomeAssistant,
    metadata: StatisticMetaData,
    statistics: Iterable[StatisticData],
    *,
    _called_from_ws_api: bool = False,
) -> None:
    """Import hourly statistics from an internal source."""
    if "mean_type" not in metadata and not _called_from_ws_api:
        report_usage(
            "doesn't specify mean_type when calling async_import_statistics",
            breaks_in_ha_version="2026.11",
            exclude_integrations={DOMAIN},
        )
    if "unit_class" not in metadata and not _called_from_ws_api:
        report_usage(
            "doesn't specify unit_class when calling async_import_statistics",
            breaks_in_ha_version="2026.11",
            exclude_integrations={DOMAIN},
        )


@callback
def async_add_external_statistics(
    hass: HomeAssistant,
    metadata: StatisticMetaData,
    statistics: Iterable[StatisticData],
    *,
    _called_from_ws_api: bool = False,
) -> None:
    """Add hourly statistics from an external source."""
    if "mean_type" not in metadata and not _called_from_ws_api:
        report_usage(
            "doesn't specify mean_type when calling async_import_statistics",
            breaks_in_ha_version="2026.11",
            exclude_integrations={DOMAIN},
        )
    if "unit_class" not in metadata and not _called_from_ws_api:
        report_usage(
            "doesn't specify unit_class when calling async_add_external_statistics",
            breaks_in_ha_version="2026.11",
            exclude_integrations={DOMAIN},
        )
'''

EXTERNAL_MEAN = "core-call-async-add-external-statistics-metadata-without-mean-type"
EXTERNAL_UNIT = "core-call-async-add-external-statistics-metadata-without-unit-class"
IMPORT_MEAN = "core-call-async-import-statistics-metadata-without-mean-type"
IMPORT_UNIT = "core-call-async-import-statistics-metadata-without-unit-class"
UPDATE_UNIT = "core-call-async-update-statistics-metadata-missing-new-unit-class"

CALLER_HEADER = (
    "from homeassistant.components.recorder.models import StatisticMetaData\n"
    "from homeassistant.components.recorder.statistics import (\n"
    "    async_add_external_statistics,\n"
    "    async_import_statistics,\n"
    "    async_update_statistics_metadata,\n"
    ")\n"
)


@pytest.fixture(scope="module")
def statistics_payload() -> list[dict]:
    """The published rules, extracted from the pinned core guards."""
    records = list(
        extract_from_source(
            "homeassistant/components/recorder/statistics.py", CORE_STATISTICS
        )
    )
    return build_rules(records, "2026.10")


@pytest.fixture(scope="module")
def statistics_rules(statistics_payload):
    return load_rules(statistics_payload)


def _findings(body, rules):
    return [
        (f.line, f.rule_id)
        for f in match_source(
            "custom_components/x/__init__.py", CALLER_HEADER + body, rules
        )
    ]


def test_the_guard_names_the_function_core_prose_misnames(statistics_payload):
    """Core's ``mean_type`` prose names the wrong function in one of the two
    places it appears, so a prose-derived rule covered only one function and
    claimed the other's call sites were the same rule. Reading the enclosing
    ``def`` instead produces both, separately."""
    assert {rule["id"] for rule in statistics_payload} == {
        EXTERNAL_MEAN,
        EXTERNAL_UNIT,
        IMPORT_MEAN,
        IMPORT_UNIT,
        UPDATE_UNIT,
    }


def test_a_mapping_key_is_not_derived_as_a_keyword(statistics_payload):
    rules = {rule["id"]: rule for rule in statistics_payload}
    assert rules[EXTERNAL_UNIT]["match"] == {
        "type": "call_missing_arg_key",
        "names": ["async_add_external_statistics"],
        "modules": ["homeassistant.components.recorder.statistics"],
        "key": "unit_class",
        "arg": "metadata",
        "arg_index": 1,
        "constructors": ["StatisticMetaData"],
    }
    # This one really is a keyword, and only when a new unit comes with it.
    assert rules[UPDATE_UNIT]["match"] == {
        "type": "call_missing_kwarg",
        "names": ["async_update_statistics_metadata"],
        "modules": ["homeassistant.components.recorder.statistics"],
        "kwarg": "new_unit_class",
        "requires": ["new_unit_of_measurement"],
    }


def test_the_message_says_where_the_option_goes(statistics_payload):
    """Core's own wording is kept, because that is what the log line says, but
    it is not the sentence the board leads with: it calls a key a keyword."""
    rules = {rule["id"]: rule for rule in statistics_payload}
    assert rules[IMPORT_UNIT]["message"] == (
        "doesn't set `unit_class` in the `metadata` passed to "
        "`async_import_statistics`, which is required from Home Assistant "
        "2026.11. Home Assistant logs it as: doesn't specify unit_class when "
        "calling async_import_statistics"
    )


def test_correct_metadata_is_never_a_finding(fixtures_dir, statistics_rules):
    findings = scan_fixture_tree(
        fixtures_dir / "statistics_metadata" / "false_positive", statistics_rules
    )
    assert findings == []


def test_missing_options_are_reported(fixtures_dir, statistics_rules):
    findings = scan_fixture_tree(
        fixtures_dir / "statistics_metadata" / "true_positive", statistics_rules
    )
    assert sorted((f.line, f.rule_id) for f in findings) == [
        (19, EXTERNAL_MEAN),
        (19, EXTERNAL_UNIT),
        (43, IMPORT_UNIT),
        (48, UPDATE_UNIT),
    ]


def test_the_reported_false_positive_stays_silent(statistics_rules):
    """barisdemirdelen/homeassistant-greenchoice#66 and
    ReikanYsora/Helios-Forecast#38, in the shape they were reported in: the
    keys are declared in the literal, which is exactly what a static scanner
    can see."""
    body = (
        "def import_readings(hass, statistic_id, readings):\n"
        "    metadata = StatisticMetaData(\n"
        "        has_mean=False,\n"
        "        has_sum=True,\n"
        "        mean_type=0,\n"
        "        name=None,\n"
        "        source='recorder',\n"
        "        statistic_id=statistic_id,\n"
        "        unit_class='energy',\n"
        "        unit_of_measurement='kWh',\n"
        "    )\n"
        "    async_import_statistics(hass, metadata, readings)\n"
    )
    assert _findings(body, statistics_rules) == []


@pytest.mark.parametrize(
    ("mapping", "reported"),
    [
        ("{'unit_class': 'energy', 'mean_type': 0}", False),
        ("{'unit_class': None, 'mean_type': None}", False),  # set is set
        ("{'mean_type': 0}", True),
        ("dict(unit_class='energy', mean_type=0)", False),  # unreadable, not empty
        ("StatisticMetaData(unit_class='energy', mean_type=0)", False),
        ("StatisticMetaData(mean_type=0)", True),
        ("StatisticMetaData(**base)", False),
        ("{**base, 'mean_type': 0}", False),
        ("build()", False),
        ("self._metadata", False),
        ("templates[statistic_id]", False),
    ],
)
def test_only_a_mapping_read_in_full_can_be_reported(
    mapping, reported, statistics_rules
):
    body = (
        "def push(self, hass, statistic_id, statistics, base, build):\n"
        f"    async_add_external_statistics(hass, {mapping}, statistics)\n"
    )
    hit = [rule_id for _, rule_id in _findings(body, statistics_rules)]
    assert (EXTERNAL_UNIT in hit) is reported


@pytest.mark.parametrize(
    ("keywords", "reported"),
    [
        ("new_statistic_id='sensor.b'", False),  # the check is not armed
        ("new_unit_of_measurement='Wh'", True),
        ("new_unit_of_measurement='Wh', new_unit_class='energy'", False),
        ("new_unit_of_measurement='Wh', new_unit_class=None", False),
        ("**changes", False),
    ],
)
def test_the_keyword_check_needs_the_keyword_that_arms_it(
    keywords, reported, statistics_rules
):
    body = (
        "def rename(hass, changes):\n"
        f"    async_update_statistics_metadata(hass, 'sensor.a', {keywords})\n"
    )
    hit = [rule_id for _, rule_id in _findings(body, statistics_rules)]
    assert (UPDATE_UNIT in hit) is reported


def test_the_mapping_is_followed_out_of_the_scope_that_built_it(statistics_rules):
    """A name is resolved in the function that uses it, then the module. A
    parameter is not resolved at all: the caller owns what is in it."""
    resolved = (
        "TEMPLATE = {'mean_type': 0, 'unit_class': 'energy'}\n"
        "\n"
        "def push(hass, statistics):\n"
        "    async_import_statistics(hass, TEMPLATE, statistics)\n"
    )
    assert _findings(resolved, statistics_rules) == []

    given = (
        "def push(hass, metadata, statistics):\n"
        "    async_import_statistics(hass, metadata, statistics)\n"
    )
    assert _findings(given, statistics_rules) == []

    shadowed = (
        "TEMPLATE = {'mean_type': 0, 'unit_class': 'energy'}\n"
        "\n"
        "def push(hass, statistics):\n"
        "    TEMPLATE = {'mean_type': 0}\n"
        "    async_import_statistics(hass, TEMPLATE, statistics)\n"
    )
    assert [rule_id for _, rule_id in _findings(shadowed, statistics_rules)] == [
        IMPORT_UNIT
    ]


def test_an_unreadable_guard_is_published_as_prose_and_discarded():
    """A "doesn't specify X" marker whose ``if`` cannot be read is not turned
    into a matcher on the strength of its prose, and the gap is recorded."""
    source = b'''
from homeassistant.helpers.frame import report_usage


def async_do_something(hass, options):
    """Do something."""
    if not _supported(options):
        report_usage(
            "doesn't specify unit_class when calling async_do_something",
            breaks_in_ha_version="2026.11",
        )
'''
    discarded: list[dict] = []
    records = list(extract_from_source("homeassistant/helpers/thing.py", source))
    rules = build_rules(records, "2026.10", discarded)

    assert [(d["symbol"], d["reason"]) for d in discarded] == [
        ("async_do_something", "unreadable_guard")
    ]
    assert [rule["matchable"] for rule in rules] == [False]
