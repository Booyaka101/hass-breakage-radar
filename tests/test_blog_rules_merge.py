"""Merging the three rule sources without publishing the same call twice."""

from __future__ import annotations

from tools.blog_rules import merge

CORE_GET_DEVICE = {
    "id": "core-call-async-get-device",
    "kind": "call",
    "symbol": "async_get_device",
    "message": "calls device_registry.async_get_device",
    "breaks_in": "2027.8",
    "source": "homeassistant/helpers/device_registry.py:1967",
    "origin": "core-ast",
    "confidence": "medium",
    "matchable": True,
    "match": {
        "type": "call",
        "names": ["async_get_device"],
        "modules": ["homeassistant.helpers.device_registry"],
    },
}

MANUAL_GET_DEVICE = {
    "id": "device-registry-async-get-device",
    "kind": "call",
    "symbol": "DeviceRegistry.async_get_device",
    "message": "hand-written",
    "breaks_in": "2027.8",
    "source": "https://developers.home-assistant.io/blog/",
    "origin": "manual",
    "confidence": "high",
    "matchable": True,
    "match": {
        "type": "call",
        "names": ["async_get_device"],
        "modules": ["homeassistant.helpers.device_registry"],
        "allow_unresolved_attribute": True,
    },
}

CORE_OTHER = {
    **CORE_GET_DEVICE,
    "id": "core-call-is-closed",
    "symbol": "is_closed",
    "match": {"type": "call", "names": ["is_closed"], "modules": ["homeassistant.components.cover"]},
}


def test_a_core_rule_matching_the_same_calls_as_a_manual_one_is_dropped():
    merged = merge([CORE_GET_DEVICE, CORE_OTHER], [MANUAL_GET_DEVICE], [], pending_floor="2026.10")
    assert sorted(r["id"] for r in merged) == [
        "core-call-is-closed",
        "device-registry-async-get-device",
    ]


def test_a_core_rule_with_no_manual_twin_is_kept():
    merged = merge([CORE_OTHER], [MANUAL_GET_DEVICE], [], pending_floor="2026.10")
    assert {r["id"] for r in merged} == {"core-call-is-closed", "device-registry-async-get-device"}


CORE_PROSE = {
    "id": "core-prose-uses-device-registry-devices-as-a-mapping",
    "kind": "prose",
    "symbol": "uses `device_registry.devices` as a mapping ...",
    "message": "uses `device_registry.devices` as a mapping",
    "breaks_in": "2027.9",
    "source": "homeassistant/helpers/device_registry.py:1560",
    "origin": "core-ast",
    "confidence": "medium",
    "matchable": False,
}

MANUAL_CONTAINER = {
    "id": "device-registry-devices-mapping",
    "kind": "attr",
    "symbol": "DeviceRegistry.devices",
    "message": "hand-written, with a matcher and the replacement",
    "breaks_in": "2027.9",
    "source": "https://developers.home-assistant.io/blog/",
    "origin": "manual",
    "confidence": "high",
    "matchable": True,
    "supersedes": [CORE_PROSE["id"]],
    "match": {"type": "container_use", "container": "devices", "uses": ["subscript"]},
}


def test_a_core_prose_rule_a_manual_rule_supersedes_is_dropped():
    merged = merge([CORE_PROSE, CORE_OTHER], [MANUAL_CONTAINER], [], pending_floor="2026.10")
    assert sorted(r["id"] for r in merged) == [
        "core-call-is-closed",
        "device-registry-devices-mapping",
    ]


def test_supersedes_names_an_id_and_nothing_wider():
    """A prose rule nobody claims stays on the board."""
    other_prose = {**CORE_PROSE, "id": "core-prose-something-else"}
    merged = merge([other_prose], [MANUAL_CONTAINER], [], pending_floor="2026.10")
    assert {r["id"] for r in merged} == {
        "core-prose-something-else",
        "device-registry-devices-mapping",
    }


def test_two_container_rules_on_different_attributes_are_not_twins():
    """``_what_it_matches`` keys on ``container`` too, or the devices and
    deleted_devices rules would read as the same rule."""
    deleted = {
        **MANUAL_CONTAINER,
        "id": "device-registry-deleted-devices",
        "supersedes": [],
        "match": {"type": "container_use", "container": "deleted_devices", "uses": ["any"]},
    }
    core_twin = {**CORE_OTHER, "id": "core-container-devices", "match": MANUAL_CONTAINER["match"]}
    merged = merge([core_twin], [MANUAL_CONTAINER, deleted], [], pending_floor="2026.10")
    assert "core-container-devices" not in {r["id"] for r in merged}
    assert {r["id"] for r in merged} == {
        "device-registry-devices-mapping",
        "device-registry-deleted-devices",
    }
