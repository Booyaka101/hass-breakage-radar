"""The service helpers whose leading ``hass`` argument goes away in 2026.10.

Core extracted these five rules automatically until 2026.10 dev deleted the
argument, and the marker with it. They are hand-written now, so what the
extractor used to guarantee -- that the shipped set still covers all five --
is what this file checks, against the examples in the announcement itself.
"""

from __future__ import annotations

import pytest

from tools.rules_engine import match_source

HELPERS = (
    "extract_entity_ids",
    "async_extract_entities",
    "async_extract_entity_ids",
    "async_extract_config_entry_ids",
    "verify_domain_control",
)
RULE_IDS = {f"core-call-{helper.replace('_', '-')}" for helper in HELPERS}

IMPORTS = (
    "from homeassistant.helpers import service\n"
    "from homeassistant.helpers.service import (\n"
    "    async_extract_config_entry_ids,\n"
    "    async_extract_entity_ids,\n"
    "    extract_entity_ids,\n"
    "    verify_domain_control,\n"
    ")\n"
    "\n"
    "\n"
)

#: The "Old" half of every example in the post, in the order it lists them.
OLD = IMPORTS + (
    "@verify_domain_control(hass, DOMAIN)\n"
    "async def do_action(call):\n"
    "    target_entry_ids = await async_extract_config_entry_ids(hass, call)\n"
    "    entity_ids = await async_extract_entity_ids(hass, call)\n"
    "    entities = await service.async_extract_entities(hass, platform.values(), call)\n"
    "    legacy = extract_entity_ids(hass, call)\n"
    "    return target_entry_ids, entity_ids, entities, legacy\n"
)

#: The "New" half. Same calls, no hass.
NEW = IMPORTS + (
    "@verify_domain_control(DOMAIN)\n"
    "async def do_action(call):\n"
    "    target_entry_ids = await async_extract_config_entry_ids(call)\n"
    "    entity_ids = await async_extract_entity_ids(call)\n"
    "    entities = await service.async_extract_entities(platform.values(), call)\n"
    "    legacy = extract_entity_ids(call)\n"
    "    return target_entry_ids, entity_ids, entities, legacy\n"
)


@pytest.fixture(scope="module")
def helper_rules(shipped_matchable_rules):
    ours = [rule for rule in shipped_matchable_rules if rule.id in RULE_IDS]
    assert {rule.id for rule in ours} == RULE_IDS, "all five must ship matchable"
    return ours


def test_every_helper_the_post_lists_is_matched(helper_rules):
    hits = match_source("custom_components/x/__init__.py", OLD, helper_rules)
    assert sorted((f.line, f.symbol) for f in hits) == [
        (10, "verify_domain_control(hass, ...)"),
        (12, "async_extract_config_entry_ids(hass, ...)"),
        (13, "async_extract_entity_ids(hass, ...)"),
        (14, "async_extract_entities(hass, ...)"),
        (15, "extract_entity_ids(hass, ...)"),
    ]


def test_the_migrated_form_is_clean(helper_rules):
    assert match_source("custom_components/x/__init__.py", NEW, helper_rules) == []
