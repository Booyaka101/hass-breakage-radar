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


def test_extracts_rules_with_a_release_shaped_breaks_in(mini_tarball, tmp_path):
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


def test_generic_symbols_are_not_turned_into_matchers():
    # `async_listen` really is deprecated in 2027.3, but it is far too common a
    # name to match on -- everybody has one.
    matcher = derive_matcher(
        "report_usage",
        "calls `async_listen` which is deprecated, use "
        "`async_subscribe_preview_feature` instead",
        "async_listen",
        "homeassistant/components/labs/helpers.py",
    )
    assert matcher is None


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
