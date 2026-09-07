# Changelog

All notable changes to Breakage Radar. Versions follow
[semver](https://semver.org/); the `custom_components/breakage_radar/manifest.json`
and `pyproject.toml` versions always agree (enforced by a test).

## 1.12.0 — 2026-09-07

### The device registry's containers, where the attribute is fine and the use is not

Home Assistant's 24 August follow-up post deprecated three more things on the
device registry, and one of them does not fit any matcher the radar had.
`DeviceRegistry.devices` is still there and still supported. What breaks in
2027.9 is *using it as a mapping*: "Using it as a mapping -- subscription,
`.get()`, `.values()`, `.keys()`, or membership by device id (`device_id in
registry.devices`) -- is deprecated." Iterating the same attribute is the
blessed replacement. Core's own code says exactly this and says it precisely:
`devices` now returns a `_DeprecatedDeviceRegistryItemsView` whose
`__getitem__`, `__contains__` and `__getattr__` call `report_usage`, while
`__iter__` and `__len__` do not.

An `attr_access` rule on `devices` would have flagged every `for device in
reg.devices` in the catalogue, which is the code people are being told to
write. So the new matcher kind names the use, not the attribute.

    {"type": "container_use", "container": "devices",
     "uses": ["subscript", "method", "membership"],
     "methods": ["get", "values", "keys", "items", "get_entry", "get_device"],
     "module": "homeassistant.helpers.device_registry",
     "registry_factory": "async_get", "registry_types": ["DeviceRegistry"]}

`reg.devices[device_id]`, `reg.devices.get(x)` and `.values()` are findings.
`for d in reg.devices`, `len(reg.devices)`, `list(reg.devices)` and handing the
view to somebody else are not, because none of them reaches a reporting method.
Membership is the one judgement call: core reports it only when the operand is
a `str`, and `device_entry in reg.devices` is the supported value test, but
nothing in a single file proves which one you wrote. The rule fires on a string
literal or on a name or attribute ending `_id`, which is how this ecosystem
spells a device id, and stays quiet otherwise. That undercounts, deliberately.

`deleted_devices` is the simpler case. Core deprecated the whole attribute,
"there is no supported public use", so the rule takes `uses: ["any"]` and any
read of it on a proved registry is a finding.

Both rules only fire on a receiver the file proves is a `DeviceRegistry`, the
same proof `device-entry-config-entries` has used since 1.5.0. `devices` is a
field on half the coordinators in this ecosystem and a rule that trusted the
spelling would be useless.

`ENGINE_VERSION` is 9, which queues every repository for a rescan.

### One walker, two matchers

The receiver proof is about a hundred lines of scope inference: parameters
resolved through annotations, walrus bindings, a registry kept on `self`,
shadowing, closures inheriting their enclosing scope. Copying it for the new
matcher would have been the obvious move and the wrong one. It is now
`_walk_typed_scopes(matcher, tree, imports)`, a generator yielding
`(node, receivers, entries)`, and both matchers consume it. What they differ on
is which node shapes count, which is the only part worth writing twice.

That is a refactor of shipped matching code, so it was measured rather than
argued. `match_source` was recorded over every rule in `data/rules.json`
against core's own 10 090 Python files plus every fixture in the repository,
through the 1.11.0 engine and through this one. The two outputs are byte for
byte identical, 293 findings each, 34 of them on the rule the walker was
extracted from. The same corpus covers the early-out below.

`container_use` skips any file whose imports cannot reach
`homeassistant.helpers.device_registry` at all, because its receiver can only
be proved through the import map. Without it the three receiver-aware rules
walked every file three times and the engine got 23% slower on a 2 000-file
core scan, which the integration would have paid for on the user's own
hardware. `attr_access_typed` deliberately does not take the same shortcut:
its `entry_params` proves a parameter from the platform contract, with no
import involved.

### Five keywords, and the dict literal that is not matched

`default_manufacturer`, `default_model` and `default_name` are deprecated on
`DeviceInfo`, with the plain `manufacturer`, `model` and `name` as the
replacement. `created_at` and `modified_at` "never had any effect, the values
were ignored", so the fix is to delete the argument. All five ship as
`call_kwarg` rules on `DeviceInfo(...)` and `async_get_or_create(...)`, the
same shape `device-info-via-device` has had since it was written.

They match the call forms only. A plain `{"default_name": ...}` dict literal
handed to something else is not matched, because nothing in the file proves
that dict is device info, and each rule says so in its own message rather than
leaving an author to guess why their code scanned clean.

One thing is not deduplicated on purpose. `registry.devices[x].config_entries`
now fires both the new 2027.9 container rule and the existing 2027.8
`device-entry-config-entries`. Those are two removals, in two releases, needing
two different edits. Collapsing them would hide the earlier deadline.

### Core's prose rules yield to the hand-written ones

Core carries markers for both container deprecations, and the extractor turned
them into prose rules with no matcher: `core-prose-uses-device-registry-devices-as-a-mapping-...`
and `core-prose-accesses-device-registry-deleted-devices-...`. With a real
matcher shipping for the same deprecation, the board was about to show each of
them twice, once with advice and once without. A manual rule can now name the
ids it replaces in `supersedes`, and the merge drops them, logging it the way
the existing coverage drop already does.

### Two rules that could never have fired

`data/rules.json` shipped `core-call-async-get-or-create-a`, matchable, looking
for `async_get_or_create(a=...)`. And `core-call-async-update-device-one`,
matchable, looking for `async_update_device(one=...)`. Nobody writes those.
The extractor reads "calls X with Y" out of core's prose, and core writes
"calls `device_registry.async_get_or_create` with a `via_device` referencing
the device itself", so the regex lifted the article. Two of the fifty-eight
matchable rules 1.11.0 published were dead, and the count said otherwise.

A derived keyword now has to be plausible as a Python one. Never a stopword
(`a`, `an`, `the`, `one`, `both`, `either`, `any`, `its`, `this`, `some`,
`no`), and if it is under four characters or carries no underscore it has to
appear verbatim as a parameter name somewhere in the same core file. That is
what keeps `unit_class` and a genuinely short keyword like `hass`, and what
`the` can never satisfy. Rejected markers are published as prose, and counted:
`counts.markers_discarded` goes from 1 to 3, with the symbol, the reason and
the core line in `discarded_markers`, so the gap is a number rather than a
silence.

Regenerating `data/rules.json` from the same core tarball
(`a556b8e9c5c6...`, core dev 2026.10) changes exactly eleven rules: the seven
added, the two superseded prose rules dropped, and the two dead matchers turned
into prose. Every other rule is byte for byte what 1.11.0 published.

### The crawl waits on the network, so it now waits in parallel and only once

Rescanning the catalogue for this release measured where the time goes: about
nine minutes of CPU across two and a half hours of wall clock, one tarball at a
time, for 4 009 repositories. The scanner was never the bottleneck. Every
rules or engine change requeues the whole catalogue, and each of those was a
day of re-downloading bytes that had not changed.

Three things, each proved on its own.

`tools/scan.py` keeps every tag tarball it downloads under `.cache/tarballs/`
and reads it from there next time. A tag never moves, so a cached one is right
for as long as the catalogue points at it; branches are never cached. A full
rescan with a warm cache is a local job of minutes. CI passes
`--no-tarball-cache`, because a fresh runner would spend longer uploading the
cache than it saved.

Downloads run `--workers` at a time (default 8), ahead of the scan, with at
most twice that many tarballs in memory. The scan itself stays on the main
thread in catalogue order, so the log, the checkpoints and the clean stop on a
429 are exactly what they were. `scan_repo` judges a download made ahead the
same way it judges its own: the exception, if there was one, is handed over
rather than raised early.

The engine now checks a file's text before it walks it. Every Python matcher
fires on an identifier from its own list, and an identifier the parser saw is
a substring of the text it parsed, so a file with none of them cannot match
and the walk is skipped. On the 2 000-file core scan used to measure the
`container_use` early-out, the whole 1.12.0 rule set went from 31.1 seconds to
3.6, with the same 27 findings. The receiver-aware walkers were the cost, and
they now run only on files that name the thing they are looking for. The two
byte-for-byte corpus diffs above were rerun with this in place, over 10 098
files: 293 findings through the 1.11.0 engine and the 1.12.0 rule set adds 16
more on core's own source, and not one file differs.

### What the re-crawl measured

`ENGINE_VERSION` 9 requeues every catalogue repository, and all 4 009 were
rescanned for this release, on the same Python 3.14 the daily crawl uses. The
control is unusually good: the daily crawl published its own pass over the same
catalogue the same day, on the old rules, so the two differ only in the rules.

| Release | Daily crawl | This release |
|---|---|---|
| 2026.10 | 14 | 14 |
| 2026.11 | 206 | 206 |
| 2027.5 | 16 | 16 |
| 2027.6 | 55 | 55 |
| 2027.7 | 36 | 36 |
| 2027.8 | 1 882 | 1 887 |
| 2027.9 | 0 | 289 |
| 2027.10 | 1 | 1 |

Every release already on the board has exactly the count it had, and not one
finding on an unchanged tag was lost, checked pairwise across all 3 981
repositories scanned both times. The five extra on 2027.8 are two
`Chance-Konstruktion` repositories that were unreachable for the daily crawl
and answered for this one, on the same tags. Everything else new is 2027.9:
289 findings across 142 repositories. Affected repositories go from 871 to 922;
matchable rules from 58 to 63.

By rule, across the whole catalogue:

| Rule | Findings | Repositories |
|---|---|---|
| `device-registry-devices-mapping` | 259 | 132 |
| `device-info-default-name` | 10 | 7 |
| `device-registry-deleted-devices` | 9 | 4 |
| `device-info-default-manufacturer` | 7 | 5 |
| `device-info-default-model` | 4 | 4 |
| `device-info-created-at` | 0 | 0 |
| `device-info-modified-at` | 0 | 0 |

`created_at` and `modified_at` find nothing across 4 009 repositories. Nobody
passes them, which is what core's own note that they never had any effect would
predict. The rules stay, because a matcher that never fires costs nothing and
missing the one integration that does costs a breakage in 2027.9, but the board
will show them at zero and that is the honest number.

Six hits were checked by hand against the raw published source, not against the
crawler's record of it:

* `AlexxIT/XiaomiGateway3` v4.2.2, `hass/hass_utils.py:226`:
  `for device in registry.devices.values():` on `registry =
  device_registry.async_get(hass)`. Dropping `.values()` is the whole fix.
* `MacSiem/ha-baby-tracker` v5.0.15, `websocket_api.py:54`: the same idiom.
* `mxshmh/ha-metric` v1.0.0, `manager.py:208`:
  `device_registry.devices.get(reg_entry.device_id)`, which becomes
  `device_registry.async_get(...)`.
* `pyalarmdotcom/alarmdotcom` v3.0.15, `__init__.py:94` and `:98`: iterates
  `device_registry.deleted_devices.values()`, then
  `del device_registry.deleted_devices[...]`. Neither has a replacement; that is
  registry bookkeeping the integration should not be doing.
* `gcobb321/icloud3` v3.5.1, `utils/entity_io.py:388`:
  `if device_id not in device_reg.deleted_devices:`.
* `tomaae/homeassistant-mikrotik_router` v2.2, `entity.py:278` and `:288`:
  `DeviceInfo(default_name=..., default_manufacturer=..., via_device=...)`,
  which is two 2027.9 findings and a 2027.8 one on the same line.

The first pass of that rescan was wrong, and the pairwise check is what caught
it. It ran on Python 3.11 while the rules were extracted on 3.14, so 1 519 files
using newer syntax (`except A, B:` is 3.14, `type X = ...` is 3.12) failed to
parse and were skipped, and 216 findings on unchanged tags disappeared without a
word. The crawler now says so when its interpreter is older than the rule set's.


## 1.11.0 — 2026-09-03

### Short deprecated names are matchable when they are scoped to a base class

Home Assistant 2026.9 removed `battery_level` from the base vacuum entity on
2 September, and the radar had never warned a single integration author about
it. Not because the marker was missing from core, but because `battery_level`
is thirteen characters. `tools/extract_rules.py` refuses to build a matcher
from a bare symbol under eighteen, for the good reason that a rule on
`async_listen` or `battery_level` alone fires on every helper in the ecosystem
that happens to share the word. The gate was right and the consequence was
that a whole class of removal was invisible.

The engine already had the answer. An `attr` matcher takes `in_class_base`,
"the enclosing class must derive from one of these", and the hand-written
device tracker rules have used it since 1.0.0. What was missing was on the
extraction side: nothing derived that scope from core's own source. It does
now. When a marker sits on a property of a Home Assistant entity base class,
the rule records the class alongside the symbol and emits

    {"type": "attr", "names": ["battery_level"],
     "in_class_base": ["StateVacuumEntity"]}

A symbol pinned to the class it is deprecated on is no longer bare, so the
eighteen-character gate does not apply to it. Bare symbols still go through
the gate and the denylist exactly as before, and no existing rule changed:
regenerating `data/rules.json` against the same core tarball produces byte-
identical output for every rule that was already there.

Core does not always name the attribute at the marker. The vacuum warning
lives in a private `_report_deprecated_battery_properties(property)` and the
names reach it as string literals from a sibling call site in the same class,
which is why nothing textual ever found them. The extractor reads those call
sites, so one marker becomes one rule per attribute it names. Run against the
real core 2026.8 source, it produces `core-attr-statevacuumentity-battery-level`
and `core-attr-statevacuumentity-battery-icon`, the two rules that would have
warned vacuum authors in the month before the removal landed. Run over the
twelve vacuum integrations in the HACS catalogue, those rules find
`Jezza34000/homeassistant_weback_component` v1.0.11, whose
`WebackVacuumRobot(StateVacuumEntity)` still declares both properties, and
report nothing on the other eleven.

Scoping is deliberately narrow. It only applies to classes integrations are
meant to subclass, which Home Assistant names `<Domain>Entity`. `ConfigFlow`,
`DeviceRegistry` and `TemperatureConverter` all carry markers of their own and
nobody overrides them, so a scoped rule there would be a matcher that can never
match.

### A call pinned to its module does not need the length gate either

The eighteen-character gate predates module pinning. Since the dolphin false
positive in 1.0.0 every auto-derived `call` matcher carries the core module
that defines the function, and the engine refuses a bare call that was not
imported from that exact module, so a rule for `is_closed` pinned to
`homeassistant.components.cover` cannot fire on anybody's own `is_closed`.
The import graph already proves what the gate was guessing at. `import_from`
rules have skipped the gate on that argument since 1.6.0; call matchers now do
too, and only the denylist still applies to them. The gate is left with the
one case that has no proof, a deprecated class name, which is `InfraredEntity`
on today's dev.

That turns four announced removals from prose into matchers:
`cover.is_closed` (2027.10), `modbus.get_hub` (2027.10),
`labs.helpers.async_listen` (2027.3) and
`TemperatureConverter.convert_interval` (2026.12). On the re-crawl one of them
found something at once: `wills106/homeassistant-solax-modbus` 2026.08.2 does
`from homeassistant.components.modbus import get_hub` and calls it at
`modbus_transport.py:31`, which puts 2027.10 on the board for the first time. A deprecated method is
pinned to its class as well as its module, which is what lets
`TemperatureConverter.convert_interval(...)` resolve; the same pin now applies
to `FlowHandler.show_advanced_options`, which had never matched anything.

Merging then had to learn one thing. The extracted `core-call-async-get-device`
matches the same calls as the hand-written `device-registry-async-get-device`,
and both firing would report every line twice. A core rule whose matcher
covers the same calls as a manual one is dropped at merge time, and the crawl
log says which.

### `in_class_base` resolves an aliased base (engine 8)

`from homeassistant.components.vacuum import StateVacuumEntity as Base` made
the class invisible to a scoped rule: the base list said `Base` and the matcher
was looking for `StateVacuumEntity`. Base names are now resolved through the
file's import map first, which also covers `classbase` rules. A relative import
is deliberately not resolved, on the same reasoning that already keeps
`from .my_registry import async_get_device` out of the device registry rule.

That gap was real, and the re-crawl proves it. `al-one/hass-xiaomi-miot` v1.1.4
declares `class XiaoxunWatchTrackerEntity(MiotTrackerEntity)` and, four lines
of file above, `class MiotTrackerEntity(MiotEntity, BaseTrackerEntity)`. The
subclass sets `_attr_location_name` at `device_tracker.py:228`, which no
version before this one reported: `XiaoxunWatchTrackerEntity` names no base the
rule knows, and its own base is in the same file. Same tag, same rule, one more
finding.

A scoped rule now also follows one level of local subclassing, so

    class BaseVacuum(StateVacuumEntity): ...
    class MyVacuum(BaseVacuum):
        @property
        def battery_level(self): ...

is a finding. A chain that leaves the file is not followed: nothing in the file
proves what `from .base import BaseVacuum` derives from, and undercounting is
the side to be wrong on.

`ENGINE_VERSION` is 8, which queues every repository for a rescan.

### What the re-crawl measured

`ENGINE_VERSION` 8 and the wider rule set each requeue every catalogue
repository, and all 4 009 were rescanned locally for this release. The daily
crawl happened to run the previous rule set over the same 4 009 repositories
the same morning, which makes the comparison unusually clean: same catalogue,
same tags, same day, different rules.

| Release | Before | After |
|---|---|---|
| 2026.10 | 11 | 11 |
| 2026.11 | 96 | 96 |
| 2027.5 | 11 | 11 |
| 2027.6 | 51 | 51 |
| 2027.7 | 29 | 29 |
| 2027.8 | 742 | 742 |
| 2027.10 | 0 | 1 |

880 affected repositories both times; 2 272 findings from 2 270. The two new
findings are exactly the two described above, `hass-xiaomi-miot` at
`device_tracker.py:228` and `solax-modbus` at `modbus_transport.py:31`, and
nothing that was found before is lost. Matchable rules go from 54 to 58;
published rules from 118 to 117, because one core rule now yields to the
hand-written twin that already covered it.

### The board says how much the gate costs

Every marker the length gate or the denylist drops is now counted during
extraction and published as `counts.markers_discarded` in `rules.json`, with
the symbol, the reason and the core line in `discarded_markers`. The board and
the README carry the total, minus any symbol a hand-written rule already covers,
so "removals with no detector" is no longer the only visible gap.
`tools/check_local.py` states the same thing about the rule set it ran, so an
author reading "OK" knows how much was looked for.

### The integration searches for the name someone would paste

A repair's "see whether it is already reported" link searched the repository
for the rule's whole symbol, and `StateVacuumEntity.battery_level` or
`async_import_statistics(missing unit_class)` finds nothing anyone wrote in an
issue title. The crawler already reduced a symbol to its bare name for its own
lookup; that helper lives in the shared engine now and the integration uses it
too.

## 1.10.0 — 2026-08-28

### Rules for the release in RC no longer retire a week early (#46)

The crawler read the current core version off core's dev branch, and dev bumps
to N+1 as soon as the N branch is cut, about two weeks before N ships. So
while a release is in RC, dev is two ahead of what anybody runs, and every
rule breaking in the RC release read as already shipped and dropped out of the
scan and the board. That is the one week a user still has time to act, which
made it the worst possible moment for a warning tool to go quiet. This cycle
that silently removed all ten 2026.9 rules while 2026.9 had not shipped.

Pending-ness is now measured against the newest release that actually shipped,
read from PyPI (`info.version` of the `homeassistant` package) and cached on
disk for six hours. A rule breaking in the release currently in RC stays
listed until that release is really out, then drops as before. `rules.json`
and the index record `latest_release` and the `pending_floor` the filters
used, so a consumer can see which comparison produced the file.

When PyPI is unreachable, answers something that is not a released calendar
version, or the run is offline, the last release it did answer is reused if
one is remembered, and otherwise the floor becomes dev minus one, the simpler
heuristic from #46. Both directions can only over-show: a just-shipped release
stays on the board for the rest of the month rather than an unshipped one
vanishing. Every run that degrades this way says so in its output instead of
doing it silently.

Reusing the remembered release matters more than it first looks. The floor
feeds `rules_hash`, and in an ordinary cycle the dev-minus-one floor sits one
release below the real one, so a single failed request would change the active
rule set, queue all 3 940 repositories for a rescan, and reverse itself the
next day. The daily crawl caches `.cache/` but the entry is always older than
its six-hour TTL by the time the next run starts, so this was the common path,
not a rare one. A remembered release is only used when its floor is no lower
than dev minus one, so a long-dead cache can never make things worse.

The board's last tile used to read "2026.10 core version", which is the dev
branch the rules came from and names a release nobody can install. It now
reads the latest released version instead.

### The board names the release that is in RC (#46)

Knowing the latest release makes the RC window itself visible, and that is the
week this issue is about. PyPI's response already lists every version it has,
so `2026.9.0b1` published alongside a newest release of `2026.8.3` says 2026.9
is in its candidate period. No extra request: the same payload was being
fetched and the pre-release list thrown away.

The board carries a "2026.9 in release candidate" tile, the crawl logs it, and
`index.json` publishes `rc_release` so any consumer can say "this ships in
days" rather than just naming a release. An RC is only claimed from a live
lookup or a fresh cache, never from a degraded one, since a remembered
candidate may have shipped in the meantime.

`index.json` also gained `pending_floor` and `pending_floor_source`. The
degradation was previously visible only to whoever read the crawl log; now the
published artifact says which comparison produced it. Both are additive, and
the shipped 1.9.1 integration was run against the new payload to prove it.

The dev-branch read stays for what it is actually for (which tarball was
scanned, the rule extraction itself); only the shipped/pending comparison
moved. Regression tests pin all three states: dev one ahead (nothing
changes), dev two ahead with the RC rule kept in a real scan, and the offline
fallback, including the 2026.12 to 2027.1 year boundary.

## 1.9.1 — 2026-08-24

Marketplace metadata only. No code change, and the action behaves identically.

`action.yml` shipped a 142-character `description`, and **GitHub Marketplace
rejects 125 or more**. That limit is only reported on the release page, after
ticking "Publish this Action to the GitHub Marketplace", so it passed every
check in CI and blocked the listing at the one point nobody automates. The
release page validated the name, icon, colour and README fine and failed on
this alone.

Shortened to 115 characters, keeping both halves of the point (what it finds,
and which release). `tests/test_action.py` now pins the limit, the nine
allowed branding colours and the thirteen Feather icons GitHub excludes, so the
next person to touch this metadata finds out in `pytest` rather than on the
release page.

Marketplace validates `action.yml` as of the release's **tag**, so the fix
needed its own tag rather than a correction on `main`. `@v1.9.0` still works as
an action; it just cannot be listed.

## 1.9.0 — 2026-08-24

### Core's other way of announcing a removal is now read (#25)

The rule extractor read `report_usage(..., breaks_in_ha_version=)` and nothing
else. Core has a second mechanism with no `breaks_in_ha_version` anywhere in
it:

```python
_DEPRECATED_TrackerEntity = DeprecatedAlias(
    _TrackerEntity, "homeassistant.components.device_tracker.TrackerEntity", "2027.6"
)
__getattr__ = partial(check_if_deprecated_constant, module_globals=globals())
```

The warning fires on the *import*. The rule set had never seen any of it.

Found by running it. 60 affected integrations in a real Home Assistant
container, with a fabricated config entry each so the code actually loads, and
the resulting deprecation log diffed against the scanner. 9 of 11 observations
already matched a finding. Both misses were this.

* **12 new rules**, every one carrying a future release: 5 in 2027.6
  (`TrackerEntity`, `ScannerEntity`, `BaseTrackerEntity`,
  `TrackerEntityDescription`, `SourceType` from
  `device_tracker.config_entry`) and 7 in 2027.8 (the `CONCENTRATION_*`
  constants from `homeassistant.const`).
* **A tenth matcher type, `import_from`**, keyed on the deprecating module
  rather than the symbol. That distinction is the whole rule: `colota` and
  `comma_ai` import `TrackerEntity` from the *replacement* path, which is the
  fix, and Home Assistant logged nothing for them. Matching the name alone
  would have flagged correct code.
* **`high` confidence.** A named import from an exact module has no receiver
  to infer and nothing to collide with, so the 18-character gate that protects
  auto-derived call matchers does not apply.
* On the 61-integration audit sample this is **13 findings across 7
  integrations**, and it catches exactly the two Home Assistant warned about.

`ENGINE_VERSION` goes to 7. The new rules change `rules_hash` anyway, so the
crawl re-scans the catalogue either way; the bump keeps the engine's identity
honest about it.

### A local "clean" only speaks for the rules it could run

`report.py` let a local scan's `clean` verdict beat an index finding whenever
the scan had run any rules at all. That is right when the local engine looked
for the rule and did not find it: the installed code is newer than the tag the
crawler scanned. It is wrong when the engine could not look at all.

An installed integration carries a vendored `rules_engine.py`. `Rule.matchable`
is `self.match.get("type") in MATCHER_TYPES`, so the day the index ships a
matcher type an installed copy predates, that copy silently skips the rule,
finds nothing, reports `clean`, and the index's finding was thrown away. The
user is told an integration is fine while the index says it breaks in 2027.6.
This is the false-all-clear class #22 recorded as a trap, and it is why #22
concluded that narrowing a rule could not afford a new matcher type.

The local scan now reports `rule_ids`, the rules it actually ran, and any index
finding whose rule is not in that list survives, attributed to the index. A
domain can now carry a local finding and an index finding at once and is still
listed once.

This removes the constraint rather than working around it: a new matcher type
is safe to ship once installs are on this, in either direction.

Found by the #25 audit, which needs a new matcher type to cover the
`DeprecatedAlias` class of removal and walked straight into it.

### The scanner runs in an integration author's CI (#21)

Every finding this project produces lands on a *user*, and a user cannot fix an
integration they did not write. `action.yml` puts the same scan in the
maintainer's own pull requests, where it reaches the one person who can act on
it.

```yaml
- uses: Booyaka101/hass-breakage-radar@v1.9.0
```

* **It annotates, it does not fail, by default.** A removal scheduled for
  2027.8 turning an unrelated pull request red is how a check gets deleted
  from a workflow file, and then it is not there for the one landing next
  month either. `fail-on: imminent` gates on what is already released or
  within `window-days` (90 by default, the same horizon the board leads with);
  `fail-on: any` is the strict release gate. The CLI still defaults to `any`,
  because a release gate is what it was already being used as.
* **Annotations and a job summary, not one or the other.** GitHub displays 10
  annotations per level per step and 50 per job, so thirty findings would show
  as ten and read as though that was all of them. The summary table carries
  every one. A finding that will fail the job is an `error`, the rest are
  `warning`s, which also puts them on separate display budgets.
* **Rules are pinned to the tag you pin.** `rules: pinned` (the default) reads
  the rule set committed at that tag, so there is no network call in anyone
  else's CI. `rules: index` fetches the published index instead, for rules
  that stay current without a version bump. The daily crawl rewrites
  `data/rules.json`, so pin an exact tag rather than a moving major.
* **Card repositories work too, unasked.** `tools/check_local.py` now reads a
  checkout without `custom_components/` as a Lovelace card repository and
  scans its JavaScript wherever it lives, deduplicating `src` against `dist`
  the way the crawler does. It used to exit 2 on all 748 plugin repositories
  in the catalogue, which would have made the action useless to every one of
  them.
* **Nothing scannable is still not clean.** A card repository whose only file
  is a minified bundle exits 2, not 0. The action's own self-test in CI
  asserts that, along with the clean, annotated, gated and card cases, by
  running the action against this repository's fixtures.

`scan_sources()` in `rules_engine.py` is the one place that decides what a
repository's Python and JavaScript add up to. The crawler reading a tarball and
the self-check reading a directory now differ only in where the bytes come
from, so a repository gets the same verdict whichever side looked at it.
Verified as a no-op on the crawler over 16 fixture repositories covering both
category branches and both status branches: identical records and findings.

### Board and README

* **The board states its own coverage gap
  ([#24](https://github.com/Booyaka101/hass-breakage-radar/issues/24)).** A new
  `removals with no detector` tile next to `active rules`, and a footer line
  spelling out what it means: at the time of writing 41 of the 98 announced
  removals have a matcher behind them, the other 57 are carried for their
  deadline only, so a repository with no findings has not been checked against
  those 57. Sourced
  from the `coverage` object already in `index.json`, so the numbers move with
  the daily crawl.
* **README answers the two questions the launch thread kept asking
  ([#23](https://github.com/Booyaka101/hass-breakage-radar/issues/23)).** How
  this differs from Spook, which inspects a running instance for what is wrong
  now and never reads integration source, and from reading
  `home-assistant.log`, which is a real answer for code that actually ran and
  silent about every branch that did not.

## 1.8.0 — 2026-08-22

### The board answers when, not just what (#3)

The second half of [#3](https://github.com/Booyaka101/hass-breakage-radar/issues/3):
1.3.0 gave the Home Assistant integration a dated schedule, but the public
board still grouped everything under bare version headings with no calendar
date anywhere. Now:

* **Three sections instead of one flat list**: anything already past its
  release date, then **Breaking within 90 days**, then everything later
  collapsed behind a `Later (633 repositories)` disclosure. A release exactly
  90 days out counts as within the window, and past releases sort newest
  first. An empty section is not rendered.
* **Every release heading carries its date and the time remaining**:
  `Home Assistant 2026.10 - 7 October 2026 - in 46 days`. The date is the
  first Wednesday of the month, Home Assistant's published schedule, computed
  by the same `release_estimated_date` the integration has used since 1.2.1
  rather than a second copy. The function now lives in `schedule.py`, vendored
  into both halves the way `rules_engine.py` is and pinned byte-identical by
  a test.
* **A hero line under the tiles**: "99 integrations break within the next
  90 days". It counts each repository once, by its earliest deadline, as does
  the `Later` count; the per-release tables still list a repository under
  every release it has a finding in, so the tables sum to more than the
  headline on purpose.
* **`index.json` carries the dates as data**: `release_date` (ISO) and
  `days_until` on every affected entry, and a top-level `release_dates` map
  per release, all computed against `generated_utc` so anyone recomputing
  gets the same integers. The index stays schema 1: the fields are additive,
  like `upstream` in 1.4.0, because every installed copy of the integration
  rejects any other schema number outright.
* **Feed item titles carry the date too**: `Home Assistant 2026.10 -
  7 October 2026`. The date never moves for a given release, so titles stay
  stable in readers.
* A release label that does not map to a date is listed under its own
  heading with a note, never dropped. Same rule as everywhere else here:
  nothing silently disappears.

Nothing changed in the integration beyond the `schedule.py` refactor, which
is behaviour-neutral and covered by the existing tests.

## 1.7.0 — 2026-08-20

### Lovelace cards are now covered

On 2026-08-19 Home Assistant [deprecated parts of the device registry
WebSocket API](https://developers.home-assistant.io/blog/2026/08/19/device-registry-websocket-api-changes/):
the device fields `config_entries`, `config_entries_subentries` and
`primary_config_entry` are replaced by `config_entry_id` and
`config_subentry_id` and removed in Core 2027.8, and the command
`config/device_registry/remove_config_entry` is replaced by
`config/device_registry/remove` and removed in Core 2027.9. That breaks
WebSocket clients rather than Python integrations, and the population that
breaks is HACS Lovelace cards, which nothing here looked at.

Now covered end to end:

* **The crawl takes the HACS plugin category** alongside integrations, from
  `data-v2.hacs.xyz/plugin/data.json` with the same `hacs/default` fallback.
  The catalogue is schema 2: every entry carries `category`, and plugins carry
  `domain: null`. 728 plugin repositories on the first fetch.
* **A tenth matcher kind, `js`**, for `.js`/`.ts`/`.mjs` source. Not a parser:
  an anchored token match, run only after `//` and `/* */` comments are
  stripped, and only in files that demonstrably talk to the WebSocket API
  (`callWS`, `sendMessagePromise`, `subscribeDeviceRegistry`, or a
  `config/device_registry` string). A card that only mentions a field in a doc
  comment can never match, and neither can a URL path segment: the first live
  crawl flagged `config_entries` inside the REST path
  `"config/config_entries/flow"` on a real card, so `/` joined the anchor's
  exclusions and that case is pinned as a test. Installs older than 1.5.0 read
  the same index and silently skip the unknown matcher kind, as they did when
  `attr_access_typed` shipped.
* **Four rules carry the blog post's exact replacement mapping**:
  `config_entries` -> `config_entry_id` (2027.8), `config_entries_subentries`
  -> `config_subentry_id` (2027.8), `primary_config_entry` ->
  `config_entry_id` (2027.8), and the remove command ->
  `config/device_registry/remove` (2027.9). All four classify as `upcoming`,
  never `broken_now`: core derives the old fields from the new ones until the
  removal, so nothing is broken today.
* **Minified bundles are skipped and counted**, not guessed at: a `.min.js`
  name or any line over 5000 characters, plus `node_modules` and vendored
  paths, are recorded as `skipped_minified` and `skipped_vendor` per
  repository and in the index coverage, so the card coverage number stays
  honest. Many card repositories publish only a dist bundle. A TypeScript card
  that also ships its compiled bundle is deduplicated to one finding per rule,
  pointing at the source file.
* **The board gained a category facet**: integrations and cards get their own
  counts, a filter, and a skipped-bundles tile.
* **The integration scans `www/community/**`**, where HACS installs cards,
  with the same js rules, and raises the same `broken_now` / `imminent` /
  `upcoming` Repairs issues with the card name in the title. A card installed
  only as a minified bundle falls back to the index's verdict on its source
  repository, joined on the repository basename, and is reported as not
  analysed rather than clean when neither side has one.

`ENGINE_VERSION` is 6, so the daily crawl rescans the full catalogue.

### `DeviceRegistry.async_get_device` is rated high confidence

### `DeviceRegistry.async_get_device` is rated high confidence

That rule is the single biggest in the set, 609 findings across 340
repositories, 36% of everything the crawl reports. It shipped at medium, which
means the board's "High only" filter hid all of it.

Checked before changing it: 197 findings across 100 of those repositories were
verified against the source at the tag that was scanned. All 197 are genuine,
none is a false positive. That sits on top of the 1.3.1 pass, which
hand-checked 45 findings, fixed the one error class with `not_awaited` and
re-scanned 36 repositories at zero. With no failures in 197, the upper bound
on the false positive rate is about 1.5%.

Considered and rejected: rewriting the matcher to prove the receiver is a
`DeviceRegistry`, the way `attr_access_typed` does for `DeviceEntry`. The rule
is already accurate, so that would have cost recall for nothing. It would also
have needed a new matcher type, and an install running an older engine skips
an unknown type, which for a rule that currently matches means a local scan
reporting clean over an index finding. Widening a rule can afford a new
matcher type; narrowing one cannot.

The board now takes each finding's confidence from the rule rather than from
the crawl record. `rules_hash` deliberately ignores confidence, so a re-rating
never invalidates a cached scan and would otherwise have sat unpublished until
each repository happened to be scanned again. Verified as a no-op on the
current crawl: all 1 695 findings already agree with their rule.

## 1.6.1 — 2026-08-19

### The feed carries releases instead of bookmarks

`feed.xml` published one item per rule, titled `2027.8: <symbol>` with a
sentence of description and a link into home-assistant/core. A reader showed
that as sixty bookmarks. Each item is now a Home Assistant release, carrying
what that release removes and which HACS integrations still use it, and
linking to that release's section of the board.

The body carries the rule list in full plus the twenty most starred affected
integrations. Embedding every affected integration would be 475 KB against a
31 KB feed, four fifths of it 2027.8 alone, growing with every crawl. As
built it is 27 925 bytes over five items, against 31 867 over sixty.

An item is news when a rule joins its release. The integration count moves
daily as the crawl widens, and dating items on that would re-notify every
subscriber several times a day, so `pubDate` is the newest first-seen date
among the release's rules. Nothing in `state/feed.json` had to change, so no
existing subscription churns.

Opening the feed in a browser now renders a page rather than raw XML. Feed
readers ignore the stylesheet, so nothing about the XML changed for them.

Nothing to update on a Home Assistant box: this is the published feed, not the
integration.

## 1.6.0 — 2026-08-18

### An ignore list in the options

A second setting under **Configure** takes integrations to leave out of the
report. Anything on it produces no finding, no notification and no count. The
list starts empty and nothing is excluded on a user's behalf.

It came from a request to drop HACS from the results. HACS is genuinely
affected, it calls `device_registry.async_get_device` in
`repositories/base.py` and that goes away in Core 2027.8, so excluding it by
default would mean hiding a true positive. Everyone installs Breakage Radar
through HACS though, so that single finding reaches every user and none of them
can act on it. Letting each user decide keeps the default honest.

The picker offers the domains actually affected on that system rather than the
catalogue, so it is a handful of rows. It carries whatever is already ignored,
since an ignored domain is filtered out of the report and would otherwise
disappear from the list that ignores it, and it accepts values outside its own
options so a stored entry survives the integration being fixed upstream and
dropping off the affected list.

`sensor.breakage_radar_affected` gained an `ignored_domains` attribute, and
diagnostics reports the setting, so a missing integration reads as a choice
rather than a bug.

## 1.5.0 — 2026-08-17

### `DeviceEntry.config_entries` is now detected

The last device-registry rule without a static check has one.
`device-entry-config-entries` (removed in Core 2027.8) shipped
`matchable: false` because the attribute name is also the ubiquitous
`hass.config_entries`, so a plain `attr_access` matcher would have flagged
nearly every integration in existence. The verdict a user saw was "check by
hand". Now the board, the index and the local scan all report it like any
other rule, at high confidence instead of low.

A ninth matcher type, `attr_access_typed`, closes it. It fires only where the
receiver is proven to be a `DeviceEntry` by single-file inference: a lookup on
a proven registry (`reg = dr.async_get(hass)` then `reg.async_get_device(...)`,
the walrus form included), a `DeviceEntry` annotation on an assignment or a
parameter, the module helpers that return device lists, or the third parameter
of `async_remove_config_entry_device`, which is a `DeviceEntry` by contract.
It is an allowlist of proven receivers, not a denylist of `hass`:
`hass.config_entries` can never match.

Inference is per scope. A name proved in one function says nothing about a
same-named local in another, which matters because `device` is one of the
commonest variable names in this ecosystem: proving them file-wide would carry
a proof out of the function that earned it. Nested scopes still inherit what
encloses them, the way a closure really does read those names, and a parameter
shadows whatever the enclosing scope proved about that spelling. A registry
kept on an attribute is proved for its whole class, since that assignment
lives in `__init__` and the lookups do not.

Verified against real code before shipping, the way 1.3.1 was: 142 HACS
integrations already hitting the sibling device-registry rules were rescanned,
and every finding the new matcher produced was hand-checked against that
repository's source at the scanned tag. 57 of 57 are genuine
`DeviceEntry.config_entries` reads across 27 repositories; none is
`hass.config_entries`.

Then measured against home-assistant/core, which is the hardest corpus there
is for this rule: 9 865 files carrying 5 963 textual `.config_entries`
occurrences, the great majority of them `hass.config_entries`. The matcher
reports 51, every one a device entry, and finds 51 of the 54 device-entry
reads present. The three it misses are handed a device by another file, which
no single-file matcher can follow; it stays quiet rather than guessing.

Installs still on 1.4.1 read the same published index and silently skip the
unknown matcher type, so nothing changes for them until they update; a pinned
test keeps that true. `ENGINE_VERSION` is now 5, which makes the daily crawl
rescan the full catalogue.

## 1.4.1 — 2026-08-13

Publishing only. The Home Assistant integration is unchanged from 1.4.0, so
updating gains you nothing unless you want the feed.

* **RSS feed at
  [`/feed.xml`](https://booyaka101.github.io/hass-breakage-radar/feed.xml)**
  ([#8](https://github.com/Booyaka101/hass-breakage-radar/issues/8), asked for
  on the announcement thread). One item per announced removal, with the release
  that does it, how many HACS integrations still use it, and a link to the
  announcement. Following the project no longer means polling `index.json` and
  diffing it yourself. The board advertises it, so readers find it on their own.

  A rule knows which release removes it, not the day it was announced, so
  `state/feed.json` records when each one was first published and the item keeps
  that date instead of looking new on every rebuild.

  Titles are capped at 72 characters and cut on a word boundary, because rules
  with `kind: prose` carry a sentence where others carry a symbol. Escaping uses
  the XML escaper rather than `html.escape`, which was turning every apostrophe
  into `&#x27;`.

## 1.4.0 — 2026-08-12

### Notifications know whether the problem is already reported

The crawler now asks each affected repository what it already says about the
deprecation, and publishes the answer in the index, so nobody has to search and
nobody files a duplicate. A notification says one of:

* **already reported and open** — links the issue and asks you to add a
  reaction there instead of opening another one
* **reported and closed** — links it and says a fix may already have shipped,
  which usually means updating is enough
* **archived repository** — says no fix is coming and to plan a replacement,
  rather than sending you to a dead tracker
* **issues disabled** — says there is nowhere to report it
* **nothing found** — links a search for the symbol, as before

Only issues that look like they are about the deprecation count. Searching a
symbol also matches tracebacks pasted into unrelated bug reports: one real
repository returned "Bug: Everything is unavailable" for a symbol search, and
linking someone to that as "the report" would be worse than saying nothing. A
title has to name the symbol or use removal language to qualify.

The lookup runs in the crawler, not on your system. It needs a GitHub token and
the search API allows 30 requests a minute, so doing it per user would need a
token from each of them. It rides along with the daily slice, so coverage grows
at the same rate as the scan itself.

New optional `upstream` field on index integrations. The index is still
schema 1 and older versions of the integration ignore it.

## 1.3.1 — 2026-08-12

* **An awaited call is no longer reported as `DeviceRegistry.async_get_device`.**
  That rule accounts for 328 of the affected integrations, so it was checked
  against real source: 44 of 45 hand-verified findings were genuine. The one
  that was not awaited its own API client's method of the same name. Core
  defines the registry method as a plain `def` taking `identifiers` or
  `connections`, so an awaited call cannot be it. Matchers gained an opt-in
  `not_awaited` constraint and that rule now sets it.

  Re-scanned against live source under Python 3.14: all 36 sampled repositories
  the rule hits are unchanged, and the false positive drops to zero. Roughly 2%
  of that rule's repositories, about seven maintainers, stop being told about a
  problem they do not have.

  Your local scan picks this up as soon as you update. The published index
  corrects itself over the following days as the daily crawl works back through
  the catalogue, which `ENGINE_VERSION` 4 forces.

## 1.3.0 — 2026-08-12

### You can now see what breaks when (#3)

The summary notification used to list affected integrations in one flat line
and their release deadlines in another, so with 13 affected you could not tell
which one broke in which release. It now shows a dated schedule:

```
2026.10 - October 2026, about 2 months away: argoclima, miele, thermia and 1 more
2026.11 - November 2026, about 3 months away: bosch, octopus_energy, spook
2027.8  - August 2027, about a year away: yandex_station
```

* Dates are written the way a person would say them, not as day counts.
* The same schedule is on the sensor as a `schedule` attribute, and every
  `details` entry gained a readable `due` field, so it is easy to build a
  dashboard card from it.
* Releases sort numerically, so `2027.10` comes after `2027.9`.

### The alert window is configurable

**Settings > Devices & Services > Breakage Radar > Configure.** Choose how far
ahead a deadline gets its own notification: 30, 60 or 90 days, 6 months or a
year. The default stays 30 days, and changing it applies immediately without a
restart.

At most five notifications are raised at once, however wide the window. A
90 day window on a system with 13 affected integrations would otherwise have
raised 13 separate notifications; the rest stay in the summary, which lists
every date anyway.

### Notifications link to where you need to go

Each per-integration notification now links straight to that integration's
releases page and its issue tracker, plus the Home Assistant change that
causes the break. The summary links the public board. Previously every
notification pointed at the same project homepage.

### The sensor no longer breaks the recorder

With nine affected integrations the sensor produced 19 KB of state attributes,
past the recorder's 16 KB limit, so Home Assistant logged a warning and stored
**none** of them. The sensor now carries a compact summary (7 KB in the same
situation, and a test pins it under the limit for 300 findings):

* `details` is now `findings`, trimmed to the fields worth templating on.
* The full report, with every message, link and version, moved to
  **Download diagnostics** on the integration page.
* `by_release` is gone. `schedule` carries the same information with dates.
* `not_in_index` is now `not_analysed`, and `clean_domains` became
  `clean_count`.

### Wording

All three notifications and the options screen were rewritten to say what
happened and what to do about it. The entity is now called "Affected
integrations" rather than "Affected", titles say "1 integration" or
"9 integrations" instead of "integration(s)", and the setup screen explains
what the integration is for rather than only how it fetches data.

## 1.2.2 — 2026-08-12

* **Setup no longer waits for the local scan** (#1). The scan ran inside the
  coordinator's first update, and config entry setup waits on that update, so
  on a system with many custom integrations adding Breakage Radar could hang
  long enough for setup to be cancelled mid-scan. The scan now runs as a
  background task: the sensor comes up with index-based results right away and
  local results replace them when the scan finishes. Refreshes behave the same
  way, so a slow scan can never block anything again.

## 1.2.1 — 2026-08-12

* **The `imminent` date estimate now uses Home Assistant's actual published
  schedule.** 1.2.0 claimed Home Assistant "publishes release numbers, not
  dates" and estimated each release as the 1st of its month. Both were wrong:
  the [release FAQ](https://www.home-assistant.io/faq/release/) states a new
  version is released **on the first Wednesday of every month**, and that rule
  matches all eight 2026 releases to the day (the 1st-of-month estimate was 23
  days of accumulated error over the same eight). The estimator now computes
  the first Wednesday, a test pins it against every real 2026 release date,
  and the wrong claim is corrected in the README and issue text. `broken_now`
  was never affected — it never used the date estimate.

## 1.2.0 — 2026-08-11

### Three levels instead of one wall of warnings

Findings are now sorted by how soon they bite, and urgency decides the
presentation:

| Level | When | How it appears |
|---|---|---|
| `broken_now` | your running Home Assistant has already reached the deadline | one **ERROR** Repairs issue per integration |
| `imminent` | the release is estimated within 30 days | one **WARNING** Repairs issue per integration |
| `upcoming` | further out | a single summary issue, grouped by release |

* The alert window is 30 days by default (`ALERT_WINDOW_DAYS`). Home Assistant
  ships monthly, landing between the 1st and the 7th, so a release label maps to
  a month; the estimate uses the 1st, which can be up to six days early and is
  never late — the right bias for a deadline warning.
* **`broken_now` is decided by version comparison alone, never by the date
  estimate**, so "already broken" stays exact even if a release slips.
* The summary issue now covers only what it still lists — anything promoted to
  its own alert leaves the group, and the summary disappears entirely when
  everything is urgent.
* New on the sensor: `imminent`, `imminent_count`, `summarised_domains`,
  `alert_window_days`, and `when` / `days_until` on every `details` entry.

### Breakage Radar no longer exempts itself

The local scan used to skip its own component. A tool that exempts itself from
its own check is a check that has quietly stopped being tested — and the
standalone `tools/check_local.py` never skipped it, so the two disagreed.
Breakage Radar is now scanned, counted and reported on like any other installed
integration, and a test runs the shipped rules over the shipped component so CI
fails if it ever uses a doomed API itself.

### Maintainability

* **`report.py` (509 lines, four responsibilities) is split** into
  `discovery.py` (what is installed), `scanner.py` (run the matchers over it)
  and `report.py` (decide what it all adds up to). Behaviour is unchanged; the
  existing suite was the safety net and every import site was updated rather
  than left behind an alias.

### Also in this release

* **`tools/check_local.py`** — a self-check for integration authors. Runs the
  published index's matchers over any checkout on disk, including forks and
  private integrations the HACS-catalogue crawl can never reach. Exits `0`
  clean, `1` with findings, `2` when it could not check (missing
  `custom_components/`, unreachable index, or no rules left), so it works as a
  release gate in an author's own CI. Also available as `breakage-radar-check`.
* **`guides/for-integration-authors.md`** — what a listing claims and what it does
  not, how to self-check before releasing, how a listing clears itself after a
  fix (cut a tag; the crawler follows releases, not the default branch), and how
  to report a false positive.
* README: dropped the internal distribution plan, refreshed the headline
  coverage figures from the live index, and linked the changelog and
  Discussions.

## 1.1.1 — 2026-08-11

Correctness fixes for defects found auditing 1.1.0 the day it shipped. Three of
the four meant a user could be told everything is fine when it is not.

* **A passed deadline no longer makes a finding disappear.** 1.1.0 reused the
  crawler's future-releases-only rule filter on the consumer side, so upgrading
  Home Assistant *onto* the release that removes an API flipped the affected
  integration to `clean` — a false all-clear at the exact moment the warning
  came true. The local scan now applies every matchable rule regardless of
  tense, and each finding is classified `upcoming` or `broken_now` (new `when`
  key on details, new `broken_now` / `broken_now_count` sensor attributes)
  against the running Home Assistant version.
* **Broken-now integrations get individually actionable Repairs issues.** One
  ERROR-severity issue per integration whose removal release has arrived —
  unlike the year-ahead aggregate these are actionable today, which is the bar
  Repairs sets. The aggregate escalates to ERROR alongside them and each issue
  clears when the integration recovers or is uninstalled.
* **A forked or renamed integration keeps its local verdict.** The scan keyed
  results by directory name while the merge looked up the manifest domain, so
  the exact case 1.1.0 was built for — a fork — had its local findings silently
  dropped into `not_in_index`. Results are now keyed by the manifest-declared
  domain; finding paths still show the real on-disk directory.
* **A degraded index can no longer launder every domain clean.** A scan armed
  with zero matchable rules proves nothing: such domains now stay `unknown`
  with a reason, and a local `clean` reached with no rules in play never
  overrides an index finding.
* **Symlinked integration directories are scanned.** The dev-checkout pattern
  (`custom_components/x` → elsewhere) was counted as installed but silently
  skipped by the scan. The top-level directory now follows the link; symlinked
  *subdirectories* are still never descended into.
* Release ordering inside a report now compares versions numerically, so
  `2027.10` sorts after `2027.5` in `by_release` and `earliest_release`.

## 1.1.0 — 2026-08-11

### The integration now scans your own code

Until now the Home Assistant integration only looked installed domains up in the
published index, so a forked, renamed or non-HACS integration — or any of the
~588 catalogued integrations the daily crawl has not reached yet — got no
verdict beyond `not_in_index`. The matcher engine that powers the crawler is now
vendored into the integration and runs over the exact bytes installed in your
`custom_components/` directory.

* **Local source scan.** `scan_installed` walks every installed integration's
  `*.py` files (skipping `__pycache__`, vendored directories and symlinks) and
  runs the same eight AST matchers the crawler uses, selected from the rules the
  index already publishes as machine-readable `match` objects. Nothing changed
  on the index side; `"requirements": []` still holds — the engine is stdlib-only.
* **Local findings replace index findings** for the same domain. They describe
  the installed bytes, which also removes the `scanned_version` /
  `installed_version` skew: for a `source: local` detail they are the same
  version by construction.
* **A domain absent from the index that parses clean is now `clean`, not
  unknown.** A domain whose files cannot be parsed stays unknown, with the
  reason in the new `not_in_index_reasons` attribute.
* **A truncated scan never reads as clean.** The sensor exposes
  `files_scanned`, `unparsed_files` and `skipped_files`; anything over the
  per-domain caps (400 files, 1 MB per file) is counted, and syntax errors,
  undecodable bytes, permission errors and unreadable directories are counted
  and swallowed, never raised.
* **Every `details` entry now carries `source: "local"` or `source: "index"`.**
* **The scan is cached** on (file count, newest mtime, total size, rules
  fingerprint, engine version), so the 12-hourly refresh re-parses nothing that
  has not changed, and it runs in an executor — the event loop is never blocked.
* Validated before shipping: five live-index repositories
  (`WulfgarW/homeassistant-pycupra`, `XiaoMi/ha_xiaomi_home`,
  `404GamerNotFound/vserver-ssh-stats`,
  `PaulAnnekov/home-assistant-padavan-tracker`, `AlexxIT/YandexStation`)
  downloaded at the exact refs the crawler scanned reproduce the index's 14
  findings rule-for-rule and line-for-line through the local scan path.

## 1.0.1 — 2026-08-08

* Keep exactly one `manifest.json` in the repository: `hacs/default` inclusion
  walks the whole clone and exits if it finds more than one, so the scanner
  fixtures now build their manifests in `tmp_path` at test time.

## 1.0.0 — 2026-08-08

* First release: the daily crawler (rule extraction from Home Assistant core,
  HACS catalogue crawl, published index + board) and the Home Assistant
  integration (index-based matching, one sensor, a Repairs issue).
