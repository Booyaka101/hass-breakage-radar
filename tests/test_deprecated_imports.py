"""Core's second removal mechanism: ``_DEPRECATED_X = DeprecatedAlias(...)``.

Found by the #25 audit. A real Home Assistant container warned that `leafspy`
imports `TrackerEntity` from the deprecated path, and no rule covered it,
because the extractor only ever read ``report_usage(breaks_in_ha_version=)``.

The declarations quoted here are verbatim from home-assistant/core dev at the
version `data/rules.json` was built from.
"""

from __future__ import annotations

from tools.extract_rules import build_rules, extract_deprecated_constants
from tools.rules_engine import Rule, ScanStats, match_source

CONFIG_ENTRY_PY = b'''
from functools import partial

from homeassistant.helpers.deprecation import (
    DeprecatedAlias,
    check_if_deprecated_constant,
)

_DEPRECATED_TrackerEntity = DeprecatedAlias(
    _TrackerEntity, "homeassistant.components.device_tracker.TrackerEntity", "2027.6"
)
_DEPRECATED_ScannerEntity = DeprecatedAlias(
    _ScannerEntity, "homeassistant.components.device_tracker.ScannerEntity", "2027.6"
)

__getattr__ = partial(check_if_deprecated_constant, module_globals=globals())
'''

CONST_PY = b'''
from homeassistant.helpers.deprecation import DeprecatedConstant, DeprecatedConstantEnum

_DEPRECATED_CONCENTRATION_PARTS_PER_MILLION = DeprecatedConstantEnum(
    UnitOfConcentration.PARTS_PER_MILLION, "2027.8"
)
_DEPRECATED_CONCENTRATION_PARTS_PER_CUBIC_METER = DeprecatedConstant(
    "p/m3", "CONCENTRATION_PARTS_PER_CUBIC_METER", "2027.8"
)
'''


def _records(path: str, source: bytes) -> list[dict]:
    return list(extract_deprecated_constants(path, source))


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #


def test_deprecated_aliases_are_extracted_with_their_release():
    records = _records(
        "homeassistant/components/device_tracker/config_entry.py", CONFIG_ENTRY_PY
    )
    assert [(r["symbol"], r["version"]) for r in records] == [
        ("TrackerEntity", "2027.6"),
        ("ScannerEntity", "2027.6"),
    ]
    assert records[0]["module"] == "homeassistant.components.device_tracker.config_entry"
    assert (
        records[0]["replacement"]
        == "homeassistant.components.device_tracker.TrackerEntity"
    )


def test_the_release_is_found_whatever_the_argument_count():
    """DeprecatedConstantEnum takes two arguments, DeprecatedConstant three,
    and the replacement path is a string too."""
    records = _records("homeassistant/const.py", CONST_PY)
    assert [(r["symbol"], r["version"]) for r in records] == [
        ("CONCENTRATION_PARTS_PER_MILLION", "2027.8"),
        ("CONCENTRATION_PARTS_PER_CUBIC_METER", "2027.8"),
    ]
    assert all(r["module"] == "homeassistant.const" for r in records)


def test_a_declaration_with_no_release_label_is_skipped():
    source = b'_DEPRECATED_Thing = DeprecatedAlias(_Thing, "some.other.Thing")\n'
    assert _records("homeassistant/thing.py", source) == []


def test_only_module_level_declarations_count():
    source = b'''
def factory():
    _DEPRECATED_Nested = DeprecatedAlias(_X, "a.b.C", "2027.6")
    return _DEPRECATED_Nested
'''
    assert _records("homeassistant/thing.py", source) == []


def test_ordinary_assignments_are_not_deprecations():
    source = b'ALIAS = DeprecatedAlias(_X, "a.b.C", "2027.6")\n'
    assert _records("homeassistant/thing.py", source) == []


def test_a_file_the_interpreter_cannot_parse_is_recorded_not_swallowed():
    unparsed: list[str] = []
    assert list(
        extract_deprecated_constants(
            "homeassistant/broken.py", b"_DEPRECATED_X = (\n", unparsed
        )
    ) == []
    assert unparsed and "broken.py" in unparsed[0]


# --------------------------------------------------------------------------- #
# the rules that come out
# --------------------------------------------------------------------------- #


def test_the_rule_is_an_import_from_matcher_at_high_confidence():
    records = _records(
        "homeassistant/components/device_tracker/config_entry.py", CONFIG_ENTRY_PY
    )
    rules = {r["id"]: r for r in build_rules(records, "2026.9")}
    rule = rules["core-import-config-entry-trackerentity"]

    assert rule["kind"] == "import"
    assert rule["breaks_in"] == "2027.6"
    # A named import from an exact module: nothing to infer, nothing to confuse.
    assert rule["confidence"] == "high"
    assert rule["matchable"] is True
    assert rule["match"] == {
        "type": "import_from",
        "modules": ["homeassistant.components.device_tracker.config_entry"],
        "names": ["TrackerEntity"],
    }
    assert "Import it from homeassistant.components.device_tracker instead" in (
        rule["message"]
    )


# --------------------------------------------------------------------------- #
# the matcher
# --------------------------------------------------------------------------- #


def _rule() -> Rule:
    records = _records(
        "homeassistant/components/device_tracker/config_entry.py", CONFIG_ENTRY_PY
    )
    payload = next(
        r
        for r in build_rules(records, "2026.9")
        if r["id"] == "core-import-config-entry-trackerentity"
    )
    return Rule.from_dict(payload)


def _hits(source: str) -> list[tuple[str, int]]:
    findings = match_source("custom_components/x/device_tracker.py", source, [_rule()], ScanStats())
    return [(f.rule_id, f.line) for f in findings]


def test_importing_from_the_deprecated_module_is_a_finding():
    assert _hits(
        "from homeassistant.components.device_tracker.config_entry import TrackerEntity\n"
    ) == [("core-import-config-entry-trackerentity", 1)]


def test_the_replacement_path_is_correct_code_and_must_not_match():
    """`colota` and `comma_ai` in the audit sample import it this way and
    Home Assistant logged nothing for them."""
    assert _hits("from homeassistant.components.device_tracker import TrackerEntity\n") == []


def test_a_different_name_from_the_deprecated_module_does_not_match():
    assert _hits(
        "from homeassistant.components.device_tracker.config_entry import ScannerEntity\n"
    ) == []


def test_an_aliased_import_is_still_the_deprecated_import():
    assert _hits(
        "from homeassistant.components.device_tracker.config_entry import "
        "TrackerEntity as TE\n"
    ) == [("core-import-config-entry-trackerentity", 1)]


def test_a_relative_import_cannot_name_the_deprecating_module():
    assert _hits("from .config_entry import TrackerEntity\n") == []


def test_a_same_named_class_of_your_own_is_not_a_finding():
    assert _hits("from .entities import TrackerEntity\n\nclass TrackerEntity:\n    pass\n") == []


def test_one_import_line_reports_once_per_name():
    hits = _hits(
        "from homeassistant.components.device_tracker.config_entry import (\n"
        "    TrackerEntity,\n"
        "    ScannerEntity,\n"
        ")\n"
    )
    assert hits == [("core-import-config-entry-trackerentity", 1)]
