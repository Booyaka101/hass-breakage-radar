"""Golden tests for the core-source rule extractor.

``tests/fixtures/core_mini.tar.gz`` is a *pinned* archive whose members are
verbatim copies of five real files from home-assistant/core ``dev``. Its sha256
is asserted here, so if the fixture is ever regenerated the golden expectations
below have to be reviewed rather than silently drifting.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile

import pytest

from tools.extract_rules import (
    KWARG_STOPWORDS,
    MIN_AUTO_SYMBOL_LEN,
    _plausible_kwarg,
    build_rules,
    core_version,
    derive_matcher,
    extract_from_source,
    iter_core_python,
    main,
    module_of,
    retain_vanished,
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


KWARG_GATE_SOURCE = b"""
from homeassistant.helpers.frame import report_usage


def async_get_or_create(self, hass, config_entry_id, via_device, unit_class):
    report_usage(
        "calls `device_registry.async_get_or_create` with a `via_device` "
        "referencing the device itself; the via device is ignored",
        breaks_in_ha_version="2027.8",
    )
    report_usage(
        "calls `device_registry.async_get_or_create` with unit_class, which "
        "no longer has any effect",
        breaks_in_ha_version="2027.8",
    )
    report_usage(
        "calls `device_registry.async_get_or_create` with hass, which is "
        "ignored",
        breaks_in_ha_version="2027.8",
    )
"""


def test_an_english_word_in_the_keyword_slot_is_not_a_matcher():
    """`_RE_CALL_WITH` reads "calls X with Y" and used to take Y on trust, so
    1.11.0 shipped `async_get_or_create(a=...)` as matchable and it could
    never fire. A keyword has to look like one, or be named in the file."""
    discarded: list[dict] = []
    records = list(
        extract_from_source(
            "homeassistant/helpers/device_registry.py", KWARG_GATE_SOURCE
        )
    )
    rules = {r["id"]: r for r in build_rules(records, "2026.10", discarded)}

    assert [(d["symbol"], d["reason"]) for d in discarded] == [("a", "not_a_keyword")]
    assert not any(
        (rule.get("match") or {}).get("kwargs") == ["a"] for rule in rules.values()
    )
    # A rejected marker is still published, as prose without a matcher.
    prose = [r for r in rules.values() if "via_device" in r["message"]]
    assert len(prose) == 1 and prose[0]["matchable"] is False


def test_a_real_keyword_still_becomes_a_matcher():
    """The gate must not cost the rules it was written around: `unit_class`
    is underscored, and `hass` is short but is a parameter in the same file."""
    records = list(
        extract_from_source(
            "homeassistant/helpers/device_registry.py", KWARG_GATE_SOURCE
        )
    )
    rules = {r["id"]: r for r in build_rules(records, "2026.10")}
    derived = {
        tuple(rule["match"]["kwargs"])
        for rule in rules.values()
        if (rule.get("match") or {}).get("type") == "call_kwarg"
    }
    assert derived == {("unit_class",), ("hass",)}


@pytest.mark.parametrize(
    ("kwarg", "params", "plausible"),
    [
        ("a", (), False),
        ("one", (), False),
        ("the", ("the",), False),  # a stopword is never rescued by a signature
        ("via_device", (), True),
        ("unit_class", (), True),
        ("hass", (), False),
        ("hass", ("hass",), True),
        ("entity", ("entity",), True),
    ],
)
def test_keyword_plausibility(kwarg, params, plausible):
    assert _plausible_kwarg(kwarg, params) is plausible


def test_shipped_rules_carry_no_dead_keyword_matcher(shipped_rules):
    """A matchable rule whose keyword is an English word is a rule that
    claims coverage it does not have."""
    for rule in shipped_rules["rules"]:
        for kwarg in (rule.get("match") or {}).get("kwargs", ()):
            assert kwarg not in KWARG_STOPWORDS, rule["id"]


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


def _one_file_core_cannot_parse(mini_tarball, tmp_path):
    """The pinned fixture with one rule-bearing file replaced by syntax this
    interpreter refuses, which is what an interpreter behind core looks like."""
    broken = tmp_path / "core_broken.tar.gz"
    with tarfile.open(mini_tarball) as src, tarfile.open(broken, "w:gz") as out:
        for member in src.getmembers():
            data = src.extractfile(member).read() if member.isfile() else b""
            if member.name.endswith("helpers/device.py"):
                data = b"breaks_in_ha_version\ndef oops(:\n"
                member.size = len(data)
            out.addfile(member, io.BytesIO(data))
    return broken


def test_a_run_that_cannot_parse_core_keeps_the_rules_already_written(
    mini_tarball, tmp_path
):
    """Core parses on a new enough interpreter, so a file that does not is this
    tool being behind it, and the run derives fewer rules than the last one did.
    Writing that over the fuller set drops every finding those rules found and
    moves rules_hash, which sends the scanner back over the whole catalogue.
    Retention does not stand in for this: a file this interpreter cannot read
    is not core having deleted what was in it."""
    output = tmp_path / "rules.json"
    assert main(["--tarball", str(mini_tarball), "--output", str(output)]) == 0
    before = output.read_bytes()

    broken = _one_file_core_cannot_parse(mini_tarball, tmp_path)
    assert main(["--tarball", str(broken), "--output", str(output)]) == 2
    assert output.read_bytes() == before


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
ISSUE_SOURCE = b"""
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _warn_about_yaml(hass, platform):
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"deprecated_yaml_{platform}",
        breaks_in_ha_version="2027.3",
        is_fixable=False,
        translation_key="deprecated_yaml",
    )


def _warn_by_variable(hass, translation_key):
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id=translation_key,
        breaks_in_ha_version="2027.4",
        translation_key=translation_key,
    )
"""


def test_a_repair_issue_is_named_by_its_translation_key():
    rules = _rules_from("homeassistant/components/netio/switch.py", ISSUE_SOURCE)
    rule = rules["core-issue-netio-deprecated-yaml-2027.3"]
    assert rule["symbol"] == "deprecated_yaml"
    assert rule["message"] == (
        "`netio` raises the `deprecated_yaml` repair issue, and the "
        "configuration it reports stops working in Home Assistant 2027.3."
    )
    assert rule["matchable"] is False


def test_a_repair_issue_keyed_by_a_variable_is_left_unnamed():
    rules = _rules_from("homeassistant/components/netio/switch.py", ISSUE_SOURCE)
    rule = rules["core-issue-netio-warn-by-variable-2027.4"]
    assert "translation_key" not in rule["message"]
    assert rule["message"].startswith("`netio` raises a repair issue")
    assert rule["symbol"] == "_warn_by_variable"


def test_two_integrations_raising_the_same_issue_are_two_deadlines():
    """One rule for both would name whichever file sorted first, and the other
    integration would read as not being affected at all."""
    rules = _rules_from("homeassistant/components/netio/switch.py", ISSUE_SOURCE)
    rules |= _rules_from("homeassistant/components/pjlink/media_player.py", ISSUE_SOURCE)
    netio = rules["core-issue-netio-deprecated-yaml-2027.3"]
    pjlink = rules["core-issue-pjlink-deprecated-yaml-2027.3"]
    assert netio["message"].startswith("`netio` raises")
    assert pjlink["message"].startswith("`pjlink` raises")


NAMELESS_TWIN = b"""
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"deprecated_yaml_import_issue_{reason}",
        breaks_in_ha_version="2027.3",
        translation_key=f"deprecated_yaml_import_issue_{reason}",
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        breaks_in_ha_version="2027.3",
        translation_key="deprecated_yaml",
    )
"""


def test_an_issue_built_at_runtime_does_not_double_up_the_named_one():
    """Both calls are the same deadline, and an f-string renders its
    placeholder as a marker, so the board would carry the marker as a name."""
    rules = _rules_from("homeassistant/components/netio/switch.py", NAMELESS_TWIN)
    issues = [rule_id for rule_id in rules if rule_id.startswith("core-issue-")]
    assert issues == ["core-issue-netio-deprecated-yaml-2027.3"]
    assert "{...}" not in rules[issues[0]]["message"]


LONG_TWIN = b"""
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


class TheVeryLongCoordinatorClassName:
    async def async_migrate_the_old_yaml_configuration(self, hass, reason):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"deprecated_yaml_import_issue_{reason}",
            breaks_in_ha_version="2027.3",
            translation_key=f"deprecated_yaml_import_issue_{reason}",
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            "deprecated_yaml",
            breaks_in_ha_version="2027.3",
            translation_key="deprecated_yaml",
        )
"""


def test_a_nameless_twin_with_a_long_id_is_dropped_too():
    """Ids are cut to 90 characters, and a rule is filed under the cut one, so
    a name kept from before the cut matches nothing."""
    rules = _rules_from(
        "homeassistant/components/a_very_long_integration_domain_name/coordinator.py",
        LONG_TWIN,
    )
    assert list(rules) == [
        "core-issue-a-very-long-integration-domain-name-deprecated-yaml-2027.3"
    ]


MOVED_SOURCE = b"""
from homeassistant.const import Platform
from homeassistant.helpers.deprecation import DeprecatedInfo

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN


class SirenSwitch:
    info = DeprecatedInfo(new_platform=Platform.SIREN, breaks_in_ha_version="2027.5.0")


class NumberSwitch:
    info = DeprecatedInfo(new_platform=NUMBER_DOMAIN, breaks_in_ha_version="2027.6.0")


class ValveSwitch:
    info = DeprecatedInfo(new_platform="valve", breaks_in_ha_version="2027.7.0")
"""


def test_a_platform_named_by_a_constant_is_read_off_the_name():
    """Core never writes these as strings: every one of the 14 call sites in
    the cached tarball says the platform in a `Platform.X` or `X_DOMAIN`
    identifier, and dropping those leaves one nameless rule for a whole
    integration's move."""
    rules = _rules_from("homeassistant/components/ring/switch.py", MOVED_SOURCE)
    assert "moves these entities to `siren`" in (
        rules["core-issue-ring-siren-2027.5"]["message"]
    )
    assert "moves these entities to `number`" in (
        rules["core-issue-ring-number-2027.6"]["message"]
    )
    assert "moves these entities to `valve`" in (
        rules["core-issue-ring-valve-2027.7"]["message"]
    )


UNRELATED_PAIR = b"""
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _warn_about_yaml(hass):
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        breaks_in_ha_version="2027.3",
        translation_key="deprecated_yaml",
    )


def _warn_about_the_other_thing(hass, key):
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id=key,
        breaks_in_ha_version="2027.3",
        translation_key=key,
    )
"""


def test_two_deprecations_in_one_release_keep_a_rule_each():
    """The nameless one is dropped as a twin of the named one, and these are
    not twins: one function's issue says nothing about another's."""
    rules = _rules_from("homeassistant/components/netio/switch.py", UNRELATED_PAIR)
    assert sorted(rules) == [
        "core-issue-netio-deprecated-yaml-2027.3",
        "core-issue-netio-warn-about-the-other-thing-2027.3",
    ]


VANISHED = {
    "id": "core-call-async-extract-entity-ids",
    "kind": "call",
    "symbol": "async_extract_entity_ids",
    "message": "passes `hass` to `async_extract_entity_ids`, where it is ignored.",
    "breaks_in": "2026.10",
    "source": "homeassistant/helpers/service.py:403",
    "origin": "core-ast",
    "confidence": "medium",
    "matchable": True,
    "match": {"type": "call_hass_argument", "names": ["async_extract_entity_ids"]},
    "expired": False,
    "occurrences": 1,
}


def _previous(rule, **over):
    return {"core_version": "2026.9.0", "rules": [dict(rule, **over)]}


def test_a_rule_core_stopped_declaring_is_kept_while_its_removal_is_ahead():
    """Core deletes the shim in the release it removes the API in, so the
    marker goes at the moment the rule matters most."""
    kept = retain_vanished(
        [], _previous(VANISHED), floor="2026.9", core_version="2026.10.0dev0",
        discarded=[],
    )
    assert [r["id"] for r in kept] == ["core-call-async-extract-entity-ids"]
    assert kept[0]["retained_since"] == "2026.10.0dev0"
    assert kept[0]["expired"] is False


def test_the_version_retention_started_is_stamped_once():
    kept = retain_vanished(
        [], _previous(VANISHED, retained_since="2026.10.0dev0"), floor="2026.9",
        core_version="2026.11.0dev0", discarded=[],
    )
    assert kept[0]["retained_since"] == "2026.10.0dev0"


def test_a_rule_retires_once_its_release_has_shipped():
    """Retention is self-limiting: the rule leaves on the release it named."""
    assert retain_vanished(
        [], _previous(VANISHED), floor="2026.11", core_version="2026.11.0dev0",
        discarded=[],
    ) == []


def test_a_marker_this_run_refused_is_not_carried():
    """Tightening a gate has to be able to take a rule out."""
    assert retain_vanished(
        [],
        _previous(VANISHED),
        floor="2026.9",
        core_version="2026.10.0dev0",
        discarded=[{"symbol": "async_extract_entity_ids", "reason": "denylisted"}],
    ) == []


@pytest.mark.parametrize("origin", ["manual", "blog"])
def test_only_rules_this_tool_derived_are_carried(origin):
    """A hand-written rule that left the file it lives in was taken out."""
    assert retain_vanished(
        [], _previous(VANISHED, origin=origin), floor="2026.9",
        core_version="2026.10.0dev0", discarded=[],
    ) == []


def test_a_rule_core_still_declares_is_not_carried_twice():
    assert retain_vanished(
        [VANISHED], _previous(VANISHED), floor="2026.9",
        core_version="2026.10.0dev0", discarded=[],
    ) == []


def test_an_unmatchable_rule_is_not_carried():
    """It found nothing while it was in core and would find nothing now."""
    assert retain_vanished(
        [], _previous(VANISHED, matchable=False), floor="2026.9",
        core_version="2026.10.0dev0", discarded=[],
    ) == []


def _core_without(mini_tarball, tmp_path, dropped):
    """The pinned fixture with one rule-bearing file emptied, which is what
    core looks like the day it deletes a deprecation shim."""
    trimmed = tmp_path / "core_trimmed.tar.gz"
    with tarfile.open(mini_tarball) as src, tarfile.open(trimmed, "w:gz") as out:
        for member in src.getmembers():
            data = src.extractfile(member).read() if member.isfile() else b""
            if member.name.endswith(dropped):
                data = b""
                member.size = 0
            out.addfile(member, io.BytesIO(data))
    return trimmed


def test_deleting_the_marker_upstream_does_not_drop_the_rule(mini_tarball, tmp_path):
    """The whole point, end to end: the two rules in helper_integration.py
    survive core deleting the file they were read from, and the board keeps
    warning about a 2027.8 removal that has not happened yet."""
    output = tmp_path / "rules.json"
    assert main(["--tarball", str(mini_tarball), "--output", str(output)]) == 0
    before = json.loads(output.read_text(encoding="utf-8"))
    ours = {
        r["id"] for r in before["rules"] if "helper_integration.py" in r["source"]
    }
    assert len(ours) == 2

    trimmed = _core_without(mini_tarball, tmp_path, "helpers/helper_integration.py")
    assert main(["--tarball", str(trimmed), "--output", str(output)]) == 0
    after = json.loads(output.read_text(encoding="utf-8"))

    assert ours <= {r["id"] for r in after["rules"]}
    assert after["counts"]["retained"] == 2
    assert after["counts"]["matchable_future"] == before["counts"]["matchable_future"]


def test_a_rule_core_renamed_does_not_come_back_beside_its_replacement():
    """Matching on the symbol too: an id that moves because a gate changed is
    the same deprecation, not a second one."""
    renamed = dict(VANISHED, id="core-call-service-async-extract-entity-ids")
    assert retain_vanished(
        [renamed], _previous(VANISHED), floor="2026.9",
        core_version="2026.10.0dev0", discarded=[],
    ) == []
