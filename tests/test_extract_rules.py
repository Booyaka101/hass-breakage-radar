"""Golden tests for the core-source rule extractor.

``tests/fixtures/core_mini.tar.gz`` is a *pinned* archive whose members are
verbatim copies of five real files from home-assistant/core ``dev``. Its sha256
is asserted here, so if the fixture is ever regenerated the golden expectations
below have to be reviewed rather than silently drifting.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from tools.extract_rules import (
    MIN_AUTO_SYMBOL_LEN,
    build_rules,
    core_version,
    derive_matcher,
    extract_from_source,
    iter_core_python,
    main,
    module_of,
)

RELEASE_RE = re.compile(r"^\d{4}\.\d+$")

PINNED_SHA256 = "95a029e7683289182b8f4d8a5383d7c5a3f9d7d712023a46bc58e25e500f8ab4"


@pytest.fixture(scope="module")
def mini_tarball(request):
    path = request.config.rootpath / "tests" / "fixtures" / "core_mini.tar.gz"
    assert path.exists(), "core_mini.tar.gz fixture is missing"
    return path


def test_fixture_tarball_sha_is_pinned(mini_tarball):
    digest = hashlib.sha256(mini_tarball.read_bytes()).hexdigest()
    assert digest == PINNED_SHA256, (
        "core_mini.tar.gz changed; review the golden expectations in this file "
        f"(got {digest})"
    )


def test_core_version_is_read_from_const(mini_tarball):
    assert RELEASE_RE.match(core_version(mini_tarball))


def test_extracts_rules_with_a_release_shaped_breaks_in(mini_tarball):
    records = []
    for path, source in iter_core_python(mini_tarball):
        records.extend(extract_from_source(path, source))
    assert records, "no deprecation call sites found in the pinned tarball"

    rules = build_rules(records, core_version(mini_tarball))
    assert rules

    versioned = [r for r in rules if RELEASE_RE.match(r["breaks_in"])]
    assert versioned, "no rule carries a breaks_in matching ^\\d{4}\\.\\d+$"


def test_golden_rule_ids_and_matchers(mini_tarball):
    records = []
    for path, source in iter_core_python(mini_tarball):
        records.extend(extract_from_source(path, source))
    rules = {r["id"]: r for r in build_rules(records, core_version(mini_tarball))}

    expected = {
        "core-call-async-device-info-to-link-from-entity": "2027.8",
        "core-call-async-device-info-to-link-from-device-id": "2027.8",
        "core-call-async-remove-stale-devices-links-keep-entity-device": "2027.8",
        "core-call-async-register-info": "2027.1",
    }
    for rule_id, release in expected.items():
        assert rule_id in rules, f"expected rule {rule_id} to be extracted"
        assert rules[rule_id]["breaks_in"] == release
        assert rules[rule_id]["matchable"] is True
        assert rules[rule_id]["match"]["type"] == "call"
        assert rules[rule_id]["match"]["modules"], "call matchers must be module-pinned"


def test_kwarg_matcher_is_derived_from_prose(mini_tarball):
    records = []
    for path, source in iter_core_python(mini_tarball):
        records.extend(extract_from_source(path, source))
    rules = {r["id"]: r for r in build_rules(records, core_version(mini_tarball))}

    rule = rules[
        "core-call-async-handle-source-entity-changes-add-helper-config-entry-to-device"
    ]
    assert rule["match"]["type"] == "call_kwarg"
    assert rule["match"]["kwargs"] == ["add_helper_config_entry_to_device"]


def test_generic_symbols_only_match_where_the_import_proves_them():
    # `async_listen` really is deprecated in 2027.3, and everybody has one.
    # Pinned to the module that defines it, the engine only fires where the
    # import graph reaches that module; with no module to pin to it is bare,
    # and a bare short name is never matched.
    what = (
        "calls `async_listen` which is deprecated, use "
        "`async_subscribe_preview_feature` instead"
    )
    pinned = derive_matcher(
        "report_usage", what, "async_listen", "homeassistant/components/labs/helpers.py"
    )
    assert pinned == {
        "type": "call",
        "names": ["async_listen"],
        "modules": ["homeassistant.components.labs.helpers"],
    }
    assert derive_matcher("report_usage", what, "async_listen") is None


def test_module_of():
    assert module_of("homeassistant/helpers/device.py") == "homeassistant.helpers.device"
    assert (
        module_of("homeassistant/components/system_health/__init__.py")
        == "homeassistant.components.system_health"
    )


def test_syntax_error_is_recorded_not_raised():
    unparsed: list[str] = []
    records = list(
        extract_from_source(
            "homeassistant/broken.py",
            b"breaks_in_ha_version\ndef oops(:\n",
            unparsed,
        )
    )
    assert records == []
    assert unparsed and "broken.py" in unparsed[0]


def test_cli_writes_a_valid_rules_file(mini_tarball, tmp_path):
    output = tmp_path / "rules.json"
    assert main(["--tarball", str(mini_tarball), "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["counts"]["matchable_future"] > 0
    assert payload["core_tarball_sha256"] == PINNED_SHA256
    assert any(RELEASE_RE.match(rule["breaks_in"]) for rule in payload["rules"])


def test_cli_offline_without_cache_fails_cleanly(tmp_path):
    assert (
        main(["--tarball", str(tmp_path / "nope.tar.gz"), "--output", str(tmp_path / "o.json")])
        == 2
    )


def test_shipped_rules_have_release_versions(shipped_rules):
    """Acceptance check 1, asserted against the committed real crawl output."""
    versioned = [r for r in shipped_rules["rules"] if RELEASE_RE.match(r["breaks_in"])]
    assert len(versioned) > 0
    assert shipped_rules["counts"]["matchable_future"] > 0


@pytest.mark.network
def test_live_core_tarball_still_yields_rules(tmp_path):
    output = tmp_path / "rules.json"
    assert main(["--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["counts"]["matchable_future"] > 0


VACUUM_SOURCE = b'''
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.frame import report_usage


class StateVacuumEntity(Entity):
    def __init_subclass__(cls, **kwargs):
        if "battery_level" in cls.__dict__:
            cls.__legacy_battery_level = True

    def _report_battery(self):
        if self.__legacy_battery_level:
            self._report_deprecated_battery_properties("battery_level")
            self._report_deprecated_battery_properties("battery_icon")

    def _report_deprecated_battery_properties(self, property: str) -> None:
        report_usage(
            f"is setting the {property} which has been deprecated",
            breaks_in_ha_version="2026.9",
        )
'''

LIGHT_SOURCE = b'''
from homeassistant.helpers.frame import report_usage


class LightEntity(Entity):
    @property
    def min_color_temp_kelvin(self) -> int:
        if self._attr_min_color_temp_kelvin is None:
            report_usage(
                "is explicitly setting `_attr_min_color_temp_kelvin` to `None`",
                breaks_in_ha_version="2027.4",
            )
        return self._attr_min_color_temp_kelvin
'''

REGISTRY_SOURCE = b'''
from homeassistant.helpers.frame import report_usage


class DeviceRegistry:
    @property
    def deleted_devices(self):
        report_usage("reads deleted_devices", breaks_in_ha_version="2027.9")
        return self._deleted
'''


def _rules_from(path: str, source: bytes, floor: str = "2026.1"):
    records = list(extract_from_source(path, source))
    return {r["id"]: r for r in build_rules(records, floor)}


def test_a_deprecated_entity_property_is_scoped_to_its_base_class():
    rules = _rules_from("homeassistant/components/light/__init__.py", LIGHT_SOURCE)
    rule = rules["core-attr-lightentity-min-color-temp-kelvin"]
    assert rule["match"] == {
        "type": "attr",
        "names": ["min_color_temp_kelvin"],
        "in_class_base": ["LightEntity"],
    }
    assert rule["symbol"] == "LightEntity.min_color_temp_kelvin"
    assert "min_color_temp_kelvin" in rule["message"]
    assert "LightEntity" in rule["message"]


def test_one_reporter_marker_becomes_a_rule_per_attribute_it_names():
    """The real 2026.9 vacuum shape: the marker names nothing itself.

    ``battery_level`` reaches ``report_usage`` as a string literal from a
    sibling call site, which is the only place the symbol appears at all.
    """
    rules = _rules_from("homeassistant/components/vacuum/__init__.py", VACUUM_SOURCE)
    level = rules["core-attr-statevacuumentity-battery-level"]
    icon = rules["core-attr-statevacuumentity-battery-icon"]
    assert level["match"] == {
        "type": "attr",
        "names": ["battery_level"],
        "in_class_base": ["StateVacuumEntity"],
    }
    assert icon["match"]["names"] == ["battery_icon"]
    assert level["breaks_in"] == "2026.9"
    assert level["matchable"] is True


def test_a_short_symbol_is_only_matchable_once_it_is_scoped():
    assert len("battery_level") < MIN_AUTO_SYMBOL_LEN
    assert derive_matcher("report_usage", "calls `battery_level`", "") is None
    assert derive_matcher(
        "report_usage",
        "calls `battery_level`",
        "",
        "",
        {"base": "StateVacuumEntity", "symbol": "battery_level"},
    ) == {
        "type": "attr",
        "names": ["battery_level"],
        "in_class_base": ["StateVacuumEntity"],
    }


def test_a_marker_outside_an_entity_class_is_never_scoped():
    """Nobody subclasses ``DeviceRegistry``, so scoping one would be dead."""
    rules = _rules_from("homeassistant/helpers/device_registry.py", REGISTRY_SOURCE)
    assert not any(
        (rule.get("match") or {}).get("in_class_base") for rule in rules.values()
    )


def test_discarded_markers_are_counted_with_a_reason():
    source = b'''
from homeassistant.helpers.deprecation import deprecated_class


@deprecated_class("Other", breaks_in_ha_version="2027.10")
class Closed:
    pass


@deprecated_class("Other", breaks_in_ha_version="2027.10")
class HubShim:
    pass


@deprecated_class("Other", breaks_in_ha_version="2027.10")
class VeryLongDeprecatedClassName:
    pass
'''
    discarded: list[dict] = []
    records = list(extract_from_source("homeassistant/components/x/__init__.py", source))
    build_rules(records, "2026.1", discarded)
    assert [(d["symbol"], d["reason"]) for d in discarded] == [
        ("Closed", "too_short"),
        ("HubShim", "too_short"),
    ]


def test_a_short_symbol_pinned_to_its_module_passes_the_gate():
    """``is_closed`` is nine characters and every cover has one, but the
    matcher only fires where the import graph proves the call reaches
    ``homeassistant.components.cover``; the gate has nothing left to protect."""
    source = (
        b'@deprecated_function("other", breaks_in_ha_version="2027.10")\n'
        b"def is_closed(hass, entity_id):\n    return True\n"
    )
    rules = _rules_from("homeassistant/components/cover/__init__.py", source)
    assert rules["core-call-is-closed"]["match"] == {
        "type": "call",
        "names": ["is_closed"],
        "modules": ["homeassistant.components.cover"],
    }


def test_a_deprecated_method_is_pinned_to_its_class():
    source = (
        b"class TemperatureConverter:\n"
        b"    @classmethod\n"
        b'    @deprecated_function("x", breaks_in_ha_version="2026.12.0")\n'
        b"    def convert_interval(cls, interval, from_unit, to_unit):\n"
        b"        return interval\n"
    )
    rules = _rules_from("homeassistant/util/unit_conversion.py", source)
    rule = rules["core-call-temperatureconverter-convert-interval"]
    assert rule["match"]["modules"] == [
        "homeassistant.util.unit_conversion.TemperatureConverter"
    ]
    assert rule["symbol"] == "TemperatureConverter.convert_interval"


def test_a_short_deprecated_class_is_still_gated():
    """A ``classbase`` matcher has no module pin, so the gate still applies."""
    source = (
        b'@deprecated_class("Other", breaks_in_ha_version="2027.6")\n'
        b"class InfraredEntity:\n    pass\n"
    )
    discarded: list[dict] = []
    records = list(extract_from_source("homeassistant/components/infrared/entity.py", source))
    rules = {r["id"]: r for r in build_rules(records, "2026.1", discarded)}
    assert not any(r["matchable"] for r in rules.values())
    assert [(d["symbol"], d["reason"]) for d in discarded] == [("InfraredEntity", "too_short")]


def test_the_denylist_still_applies_to_a_pinned_symbol():
    source = (
        b'@deprecated_function("x", breaks_in_ha_version="2027.10")\n'
        b"def async_added_to_hass(self):\n    return None\n"
    )
    discarded: list[dict] = []
    build_rules(
        list(extract_from_source("homeassistant/helpers/entity.py", source)),
        "2026.1",
        discarded,
    )
    assert [d["reason"] for d in discarded] == ["denylisted"]


def test_a_denylisted_symbol_is_counted_as_denylisted():
    source = (
        b'@deprecated_function("x", breaks_in_ha_version="2027.10")\n'
        b"def async_will_remove_from_hass(self):\n    return None\n"
    )
    discarded: list[dict] = []
    build_rules(
        list(extract_from_source("homeassistant/helpers/entity.py", source)),
        "2026.1",
        discarded,
    )
    assert [d["reason"] for d in discarded] == ["denylisted"]


def test_shipped_rules_publish_the_discarded_marker_count(shipped_rules):
    counts = shipped_rules["counts"]
    assert counts["markers_discarded"] >= counts["markers_discarded_pending"]
    assert len(shipped_rules["discarded_markers"]) == counts["markers_discarded"]


def test_a_private_core_class_is_not_treated_as_an_entity_base():
    """``_TemplateCameraEntity`` is named like one and is core-internal."""
    source = b'''
from homeassistant.helpers.frame import report_usage


class _TemplateCameraEntity:
    @property
    def frame_interval(self):
        report_usage("reads frame_interval", breaks_in_ha_version="2027.9")
        return 1
'''
    rules = _rules_from("homeassistant/components/template/camera.py", source)
    assert not any(
        (rule.get("match") or {}).get("in_class_base") for rule in rules.values()
    )
