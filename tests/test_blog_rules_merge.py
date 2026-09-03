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
