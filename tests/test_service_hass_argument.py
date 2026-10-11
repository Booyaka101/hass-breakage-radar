"""The service helpers whose leading ``hass`` argument went away in 2026.10.

Core extracted these five rules automatically until 2026.10 dev deleted the
argument, and the marker with it. They are hand-written now, so what the
extractor used to guarantee -- that the shipped set still covers all five --
is what this file checks, against the examples in the announcement itself.

2026.10 shipped on 7 October 2026, and the floor moved past it on the next
crawl, so since then the five are retired rather than shipped: a rule retires
in the release it names, hand-written or not. Both phases of that life are
held here. The matchers are exercised against the announcement straight from
``data/manual_rules.json`` whichever phase the floor is in -- that file is
what outlives the marker, and what the next ``call_hass_argument`` rule is
written against -- and the shipped set is required to carry all five only
while their removal is still ahead of the floor it was built with.
"""

from __future__ import annotations

import pytest
from conftest import REPO_ROOT

from tools.blog_rules import load_manual_rules
from tools.release import floor_from_payload
from tools.rules_engine import is_pending, load_rules, match_source

MANUAL_RULES = REPO_ROOT / "data" / "manual_rules.json"

HELPERS = (
    "extract_entity_ids",
    "async_extract_entities",
    "async_extract_entity_ids",
    "async_extract_config_entry_ids",
    "verify_domain_control",
)
RULE_IDS = {f"core-call-{helper.replace('_', '-')}" for helper in HELPERS}

BREAKS_IN = "2026.10"

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
def helper_rules():
    """The five matchers, straight from the file that hand-writes them.

    Not from the shipped set: that set retires them once 2026.10 has shipped,
    and the matcher deserves the same coverage on both sides of that day.
    """
    hand_written = {rule["id"]: rule for rule in load_manual_rules(MANUAL_RULES)}
    assert RULE_IDS <= set(hand_written), "manual_rules.json must keep all five"
    return load_rules(hand_written[rule_id] for rule_id in sorted(RULE_IDS))


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


def test_the_five_ship_only_until_their_release_lands(shipped_rules, shipped_matchable_rules):
    """Matchable in full while 2026.10 is ahead of the floor, retired after."""
    floor, _source = floor_from_payload(shipped_rules)
    shipped = {rule.id for rule in shipped_matchable_rules if rule.id in RULE_IDS}
    if is_pending(BREAKS_IN, floor):
        assert shipped == RULE_IDS, "2026.10 is still ahead; all five must ship"
    else:
        assert shipped == set(), "2026.10 shipped; the five retired with it"
