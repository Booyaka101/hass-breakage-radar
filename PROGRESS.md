# PROGRESS — hass-breakage-radar

Status at the end of the v1.10.0 session (2026-08-28); earlier sections are
kept as history, newest first.

## v1.10.0 — pending is measured against the released core, not dev (2026-08-28)

Issue #46, the one-release overlap #45 left open. Dev bumps to N+1 at branch
cut, two weeks before N ships, so during the RC window dev is two ahead of
stable and `is_pending` against dev retired every rule for the RC release a
week early. Live proof on the day of the fix: dev 2026.10, stable 2026.8,
and the published index carried zero of the ten 2026.9 rules.

The design that kept the diff small: nothing about `is_pending` or
`matchable_rules` changed, only the *value* passed to them. `tools/release.py`
resolves the newest release PyPI has seen for `homeassistant` (6 h disk cache
in `.cache/latest_release.json`) and turns it into a **pending floor**, the
first release nobody runs yet. `extract_rules.py` records `latest_release` /
`pending_floor` / `pending_floor_source` in `rules.json`; `blog_rules`,
`scan` and `build_index` read the floor back with `floor_from_payload()`,
which also handles a rules.json from before the fields existed. The engine is
untouched apart from the `is_pending` docstring (kept byte-identical in the
vendored copy), so old installs are unaffected and `ENGINE_VERSION` stayed 7.

Fallback semantics worth remembering: when PyPI fails, answers a pre-release,
answers something at-or-past dev, or the run is offline, the floor is dev
minus one, per the issue. That direction only over-shows a shipped release
for the rest of the month, never hides an unshipped one, and every degraded
run warns. During an RC window the fallback floor *equals* the PyPI floor, so
a PyPI outage in exactly the sensitive weeks changes nothing.

VERIFIED (all by hand on this machine, 2026-08-28):
* pytest 387 passed, 3 skipped (was 370/3). Ruff at the pre-existing
  baseline; both new files clean. Clone check: highest new-function pair 17%.
* Real pipeline in a scratch root: extract resolved core dev 2026.10, latest
  2026.8 via PyPI, floor 2026.9; the ten 2026.9 rules came back non-expired,
  and the rebuilt index published 125 rules vs the committed 115 with
  affected (855) and findings (2252) byte-identical.
* Offline path run for real (`--offline`, no PyPI cache): warning printed,
  floor 2026.9 via dev-minus-one, same `rules_hash`, and a live scan of
  404GamerNotFound/vserver-ssh-stats produced the same 7 findings.
* The RC boundary is pinned through a real scan: a fixture tarball with a
  2026.9 `setup_scanner` rule produces its finding under the floor, retires
  once the floor passes 2026.9, and the year boundary 2026.12 -> 2027.1 is
  asserted both in arithmetic and in `is_pending`.

Trap dodged: the tests must never resolve against the developer's real
`.cache/latest_release.json` or the network, so `tests/conftest.py` gained an
autouse fixture pointing the cache at `tmp_path` and making the fetch refuse.

### What the self-review then found, and it was the more interesting half

The first version fell straight from a failed PyPI request to dev minus one,
and that is a worse failure than it looks. **The floor feeds `rules_hash`.** In
an ordinary cycle (dev N+1, stable N) the dev-minus-one floor sits one release
*below* the true floor, so a single failed request changes the active rule
set, queues all 3 940 repositories for a rescan, and reverses itself the next
day. Measured on the real rule set for the cycle where it first bites: healthy
floor 2026.11 gives 49 rules / hash `9d958db`, the fallback floor 2026.10
gives 54 / `4347abb`.

Not a rare path either. The crawl workflow *does* cache `.cache/`
(`actions/cache@v6`), but the entry is always older than its six-hour TTL by
the time the next daily run starts, so every crawl re-fetches and any blip
takes the fallback.

Fixed with stale-while-error: an expired entry still names a version that
really shipped, so its floor is a valid lower bound and usually the exact one.
It is used only when it is no lower than dev minus one, so a months-dead cache
can never make things worse, and dev minus one remains the last resort. Both
degraded paths announce themselves. Verified by simulating the outage against
the real rule set: hash held at `4347abb` this cycle and at `9d958db` next.

Three smaller things from the same pass:
* **The board's last tile said "2026.10 core version".** That is the dev branch
  the rules came from, naming a release nobody can install, on the public page
  whose whole subject is which release breaks you. It now reads the latest
  released version, and the README headline numbers were refreshed against the
  live index (3 940 crawled, 855 affected, 2 252 findings) with a new
  screenshot.
* **Old installs were proven unaffected, not assumed.** `latest_release` is
  additive, so the shipped v1.9.1 `report.py` was loaded from the tag and run
  against the new index: it produces a byte-identical report with and without
  the key. Same check the `upstream` field got at 1.4.0.
* **No feed burst.** Ten 2026.9 rules return to the index, and none of them has
  a `state/feed.json` first-seen date, so the worry was ten items dated today.
  They are all non-matchable, no integration has a 2026.9 finding, and the feed
  carries one item per release *with* affected integrations, so it stays at 6.

The integration side needed no change and that was checked rather than
assumed: `matchable_rules` is defined in the vendored engine but never called
there. The local scan runs every matchable rule regardless of tense, which is
the 1.1.1 contract (a deadline that has passed must still fire as
`broken_now`), and `report.py` compares against the user's *running* version
with `is_future`, which is the comparison that was always correct.

## v1.9.0 — the backlog session: five issues, and one found by running it (2026-08-24)

Worked the open issues rather than a single feature. #19 was already shipped
and just needed closing; #24, #23, #21 and #25 all landed; #22 got its record
corrected. Four PRs, all squash-merged with every check green on the exact
commit.

### #24 + #23 — the board states its gap, the README answers Spook (PR #36)

The board showed "41 active rules" and nothing about the 57 announced removals
with no matcher. A tile plus a footer line, both from the `coverage` object
already in `index.json`. The wording was the work, not the code: checked what
the 57 actually are before writing it (46 `core-ast`, 11 `blog`, and a real
chunk of the `core-ast` ones are core migrating its own integrations, e.g.
`core-issue-opnsenseconfigflow-*`), so the line says that rather than claiming
57 blind spots that affect anybody.

README gained a section on Spook, checked against spook.boo rather than
written from memory, and on reading `home-assistant.log`, which gets the
honest version including that the log beats the scanner on precision.

### #21 — the scanner runs in an author's CI (PR #37)

`action.yml`, composite, annotating by default and not failing. `fail-on`
reuses the alert-window idea (`never` / `imminent` / `any`), rules come pinned
from the tag so there is no network call in anyone else's CI, and findings go
out as annotations *and* a job summary because GitHub only shows 10
annotations per level per step.

Two things worth keeping:
* **The issue said settle the five design questions before writing any of it,
  so nothing was written first.** Posted a recommendation on each with the
  reasoning, then built it. One recommendation was wrong and got reversed
  during the build: I proposed a quiet no-op for card repositories, but the
  exit-code contract in `test_check_local.py` says "could not check" must
  never read as clean, and a silent green is exactly that. Closed the gap
  instead — `check_local.py` now reads a checkout without `custom_components/`
  as a card repository. It used to exit 2 on all 748 plugin repos.
* **`scan_sources()` was extracted rather than writing a fourth scan loop.**
  `tools/scan.py` and `check_local.py` now differ only in where the bytes come
  from. Proved it a no-op instead of arguing it: old and new `scan_repo` over
  16 tarballs built from every fixture tree, both categories for every tree,
  identical records and findings, both status branches hit. Stopped at
  `custom_components/.../scanner.py`, which has caps, caching and an executor
  and was already deduplicated in #34.

### #25 — the audit, which found a whole mechanism (PRs #38, #39)

Ran what the issue proposed. 60 affected integrations installed at the exact
ref the crawl scanned, plus a fabricated config entry per domain written into
`.storage` — without that Home Assistant never imports a custom integration at
all, and the audit would have measured nothing. HA 2026.8.2 in Docker, two
runs (`warning` then `info`, logs merged because `report_usage` dedupes per
process). 61 domains installed, 57 set up, 11 deprecation observations.

**9 of 11 already matched a finding. Both misses were the same thing**, and it
was not a missing rule but a missing mechanism:

```python
_DEPRECATED_TrackerEntity = DeprecatedAlias(
    _TrackerEntity, "homeassistant.components.device_tracker.TrackerEntity", "2027.6"
)
__getattr__ = partial(check_if_deprecated_constant, module_globals=globals())
```

No `breaks_in_ha_version` anywhere in it, so `extract_rules.py` had never seen
any of it. 12 declarations in core, every one with a future release.

* 12 new rules, a tenth matcher type `import_from`, `high` confidence.
* **Keyed on the deprecating module, never the symbol.** The audit is what
  proves that matters: `colota` and `comma_ai` import `TrackerEntity` from the
  *replacement* path and Home Assistant correctly logged nothing for them.
* Old and new extractor over the same tarball: the 114 existing rules are
  byte-identical and the unparsed list is identical.

### The trap the audit walked into, and #22's rule of thumb

#22 recorded "widening a rule can afford a new matcher type. Narrowing one
cannot." Checking that before writing `import_from` showed the first half is
wrong. `report.py` had:

```python
elif local and local.get("status") == "clean" and rules_in_play > 0:
    clean.append(domain)
```

`rules_in_play` is a *count*. An install one version behind runs the rules it
understands, skips the unknown matcher type, finds nothing, reports `clean`,
and that clean discards the index finding. The user is told an integration is
fine while the index says 2027.6. #17 was safe for a narrower reason than
"widening": its rule shipped `matchable: false`, so there was no index finding
to bury.

Fixed rather than worked around (PR #38, merged first and deliberately
sequenced ahead of the rules). The scan now reports `rule_ids` and `report.py`
keeps any index finding whose rule it could not run. A `clean` speaks only for
what was actually checked. That removes the constraint in both directions,
including the narrowing case #22 had to rule out. Commented the correction on
#22 since it is kept as a record.

### Two harness lessons

* **The audit manufactured its own false gaps.** The first pass ran the scan
  side on Python 3.11 and reported four gaps; two were files 3.11 cannot
  parse, silently contributing nothing to match against. Exactly the failure
  the README warns users about, biting the audit. Anything doing this has to
  parse with the crawler's 3.14 and print the unparseable count.
* **A branch touching `docs/` conflicts with the daily crawl within hours,**
  and GitHub then skips `pull_request` workflows entirely rather than failing
  them. That is #18's failure mode reaching `docs/` instead of
  `data/rules.json`. The first push of PR #36 produced no `Validate` run at
  all and it is invisible unless you look for the *absence* of a check.

VERIFIED (all by hand on this machine, 2026-08-24):
* pytest 339 passed, 3 skipped (was 300/3). Ruff at the pre-existing baseline;
  every new file clean.
* Clone check per the 1.7.0 lesson: difflib over new functions against the
  ones they parallel. Highest pair among anything written here is 39%
  (`extract_deprecated_constants` vs `extract_from_source`, since reduced to
  30% by extracting the shared parse-or-record), `read_python` vs
  `read_javascript` 5%, `render_text` vs `render_github` 6%.
* The action's shell step run locally over all five self-test cases before it
  was ever pushed: exits 0, 0, 1, 0, 2 as intended. It then passed as a real
  CI job.
* New rules re-run over the audit sample: 13 findings across 7 of 61
  integrations, catching exactly the two Home Assistant warned about, with
  `colota` and `comma_ai` staying clean.

SHIPPED (2026-08-24): PRs #36, #37, #38, #39, squash-merged as `29cbc03`,
`5a6ff09`, `80ef3df`, `700cb74`. Issues #19, #23, #24, #21, #25 closed. #22
left open with the correction recorded; #3 left open for DunLaoghaire1.
Released as <https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.9.0>,
tagged at the crawl commit `dee1d09` rather than the last PR merge, so the
action's default `rules: pinned` reads a rule set that actually contains the 13
import rules. Crawl commits carry no pytest checks (a `GITHUB_TOKEN` push does
not trigger workflows), so `validate.yml` was dispatched on that exact sha
first and all 12 checks confirmed green.

Round trip verified after release: fetched `action.yml` and `data/rules.json`
from the published tag, ran the action's shell step against a leafspy-shaped
integration, and got line 4 (`config_entry import TrackerEntity`) flagged with
line 3 (the replacement path) clean, `fail-on: never` exit 0 and `fail-on: any`
exit 1.

### After the release, same day

* **The conflicted-PR blind spot now has a watchdog (PR #41).** The failure
  mode recorded above is that GitHub *skips* a conflicted pull request's checks
  rather than failing them, and nothing inside the repository can report it
  because the reporting workflow is the one being skipped.
  `tools/pr_health.py` runs daily from the default branch, 23 minutes after the
  crawl, labels what GitHub calls `CONFLICTING` and explains once what the
  missing checks mean. Two things the tests caught: clearing the label on
  `UNKNOWN` would have dropped a real warning, and `gh pr list` can report
  `UNKNOWN` because mergeability is computed lazily, so it is re-asked rather
  than taken at face value. Dispatched in production and confirmed reporting
  `0 open pull request(s), 0 action(s)`; both real label paths exercised
  against PR #41 and cleaned up.
* **Two details from self-reviewing the session's own diff (PR #42).** The
  `scan_sources` extraction had quietly changed which files `check_local` walks
  (dotted files are now read, which matches the crawler and is the behaviour
  worth having, but it was an accident) and `pr_health.apply` bailed on the
  first pull request it could not update, dropping the rest.
* **Measured the new rules against the README's own bar**, that a rule firing
  on a large fraction of the catalogue is a tax rather than a signal. Two
  dispatched crawl slices, 1 600 repositories rescanned: 97 repo-hits across
  the 13 import rules, widest single rule 35 repositories, against
  `device-registry-async-get-device` at 152. Affected went 742 -> 779 and a new
  2027.6 bucket appeared with 19 integrations. Five of the 13 rules have no
  hits yet. Still partial: `rules_hash` changed, so the whole catalogue needs a
  rescan and the daily job is still rotating through it.

## v1.8.0 — the board answers when, not just what (2026-08-22)

The second half of issue #3 (DunLaoghaire1): 1.3.0 dated the integration's
schedule, but the public board still grouped everything under bare version
headings. Now the board leads with what breaks within 90 days, collapses the
rest behind "Later (633 repositories)", and every heading carries the date:
"Home Assistant 2026.10 - 7 October 2026 - in 46 days". A hero line under the
tiles reads "99 integrations break within the next 90 days". The index gained
`release_date` + `days_until` per entry and a `release_dates` map per release,
computed against `generated_utc` so the integers are reproducible.

Design decisions worth keeping:
* **The index stays schema 1.** The brief asked for a schema bump, but every
  installed copy's `validate_index` hard-rejects any schema other than 1
  (documented at 1.4.0, when `upstream` was added additively for exactly this
  reason). Bumping would have bricked the index fetch on every deployed
  install. The new fields are additive instead.
* **The date logic exists once.** `release_estimated_date` / `describe_when`
  moved from `report.py` into `schedule.py`, vendored byte-identically into
  `tools/` the way `rules_engine.py` is, with the same kind of identity test.
  The board, the feed and the integration all call the same functions.
* **Two counting modes, deliberately.** Bucket summaries (hero, "Later (N)")
  count each repository once by its earliest deadline; the per-release tables
  keep listing a repository under every release it has a finding in. Summing
  table rows would double-count and the code comments say so.
* A release label that maps to no date renders under "No release date" with a
  visible note, never dropped; empty buckets render nothing; past releases
  sort newest-broken first; exactly 90 days out counts as within the window.

VERIFIED (all by hand on this machine, 2026-08-22):
* pytest 298 passed, 3 skipped (was 283/3). Ruff at the pre-existing 20-error
  baseline (the one new error introduced was fixed).
* Real `build_index.py` run over the live crawl (3 905 scanned, 732 affected)
  reproduced the worked example exactly: hero 99, Later 633, 2026.10 =
  2026-10-07 / 46 days, 2026.11 = 2026-11-04 / 74 days, 2027.5/7/8 collapsed.
* Board screenshot taken in headless Chrome: hero line, dated headings and
  pills all render; `<details id="later">` ships without `open`.
* `docs/feed.xml` parses as XML; item titles now
  "Home Assistant 2026.10 - 7 October 2026".
* Trap avoided: `Set-Content -Encoding utf8` (PS 5.1) writes a BOM; the first
  version bump corrupted pyproject/manifest and was redone via python.

SHIPPED (2026-08-22): PR #35, all 9 PR checks green, squash-merged as
`914240e`, all 12 main checks green including the Pages deploy. Live board,
index.json and feed.xml all verified serving the new content. Released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.8.0>.
Replied on issue #3 with the live link; the issue stays OPEN for
DunLaoghaire1 to confirm, per standing practice (the brief said close it;
the reporter-confirms rule wins).

## v1.7.0 — Lovelace cards are covered (2026-08-20)

Home Assistant's 2026-08-19 post deprecates the device-registry WebSocket
fields (`config_entries`, `config_entries_subentries`, `primary_config_entry`
-> `config_entry_id` / `config_subentry_id`, removed 2027.8) and the
`config/device_registry/remove_config_entry` command (-> `.../remove`, removed
2027.9). That breaks HACS Lovelace cards, a surface we did not crawl at all.
This release adds it: the plugin catalogue (schema 2, `category` on every
entry, plugins carry `domain: null`), a tenth matcher kind `js` (anchored
tokens over comment-stripped `.js`/`.ts`/`.mjs`, gated on the file referencing
the WebSocket API), four manual rules carrying the post's exact replacement
mapping, extension dispatch in the crawler with `skipped_minified` /
`skipped_vendor` counted per repo and in coverage, a category facet on the
board, and a `www/community/**` scan in the integration with card-worded
Repairs issues. ENGINE_VERSION is 6.

VERIFIED (all by hand on this machine, 2026-08-20):
* Ran the branch in a real Home Assistant container (docker, port 8124, the
  v1.3.0 method; reproduce with `.cache/seed_1_7_0.py`, the docker run in its
  header, then `.cache/ha_onboard.py`), seeded with pycupra plus three cards
  under `www/community`: adguard-card as source, mini-graph-card as its real
  released minified bundle, and a card that exists in no index. The local scan
  found adguard-card at the same file and line the crawler found in its
  repository, the unknown card got a local verdict, and the bundle-only card
  landed clean through the index's `clean_cards` with the skip counted
  (56 card files scanned, 1 minified skipped). The summary Repairs issue reads
  "3 custom integrations and cards" with both cards on the dated schedule
  (verified over the WebSocket API), the options picker offers the cards, and
  ignoring one removed it from the report, the counts and the picker's
  results. Diagnostics carries the full card section.
* pytest 283 passed, 3 skipped (was 232/3). New offline fixtures under
  tests/fixtures/plugins cover the brief's worked example byte for byte, the
  comment-only negative, minified and vendor skips, and the TS-plus-bundle
  dedupe.
* Full plugin crawl, live: 728/728 plugin repositories scanned, 4 affected,
  716 clean, 8 unreachable (recorded, no crash), 308 minified bundles and
  2 963 vendored files skipped and counted. All 4 findings hand-verified as
  genuine runtime reads of `device.config_entries` at the exact tag and line
  (unifi-device-card, adguard-card, pi-hole-card, toothbrush-card).
* Two false-positive classes found by the live crawl and killed before ship,
  both pinned as regression tests: `config_entries` inside the REST path
  "config/config_entries/flow" (ha-pluviometer-card), and a TypeScript
  interface member that types the field but never reads it (ha-sankey-chart).
  The token anchor now excludes `/` neighbours and an immediate `:`/`?:`.
* Clean `pip install` in a fresh 3.12 venv; `breakage-radar-catalog
  --force-fallback` produced a real two-category catalogue from the
  `hacs/default` fallback (fallback path live-verified, 733 plugins).
* Board renders 681 affected integrations + 4 affected cards, a category
  filter, card badges, and the skipped-bundles tile.

Trap hit and cleaned up: a scan slice on this box's bare Python 3.11 rescanned
122 integrations and would have committed under-reported records (PEP 695
files fail to parse on 3.11, see LESSONS 2026-08-08/-11) with the new
rules_hash, which would have stopped the daily 3.14 crawl from ever
correcting them. Those 122 records were restored from HEAD, so the daily job
rescans every integration under ENGINE_VERSION 6 as designed. Plugin scans are
regex-only and interpreter-safe, so the 728 plugin records stand.

Also hit: two concurrent scan.py processes clobber each other's findings.json
(whole-file checkpoints, last writer wins). Ran the remaining slices strictly
sequentially; nothing structural changed.

## The daily crawl was silently switching off CI on open PRs (2026-08-17)

Noticed while shipping 1.5.0: two of the five pushes to that branch produced no
`Validate` run at all, while the other three ran normally. Not a failure, no
run. It looks exactly like a branch whose checks are green, because the only
thing left on the commit is CodeQL, which runs on push.

Cause, and the correlation is 5 for 5: the crawl commits `data/rules.json`
every day. The rule set itself rarely changes, but `generated_utc`,
`blog_merged_utc` and `core_tarball_sha256` move on every run, so any open pull
request that touches the file conflicts within a day. **GitHub cannot build
`refs/pull/N/merge` for a conflicted pull request, so it skips that PR's
`pull_request` workflows entirely.** The two pushes made while the branch was
conflicted got nothing; the three made while it was mergeable all ran.

Fixed by not committing a provenance-only rewrite: `tools/rules_changed.py`
compares the regenerated file against the committed one ignoring those stamps,
and the crawl stages `data/rules.json` only when a rule really changed.
Verified on the real file, where a fresh `blog_rules.py` run produces exactly
one changed line and the tool correctly declines it.

Not a release: the shipped integration is unchanged, so there is nothing for a
user to update to. The published index carries the rules either way, and
`build_index.py` stamps it with its own timestamp, so the board stays dated
daily as before.

Worth keeping in mind: a conflicted PR cannot be merged anyway, so nothing
could have shipped unchecked. The cost was diagnostic, not correctness.

## v1.5.0 — DeviceEntry.config_entries finally has a matcher (2026-08-17)

The one device-registry rule shipped `matchable: false` (the name collides with
`hass.config_entries`) is now detected by a ninth matcher type,
`attr_access_typed`: two flow-insensitive passes per scope prove
DeviceRegistry-typed names, then DeviceEntry-typed names (registry lookups
including the walrus form, annotations on assignments and parameters, the
module-level `async_entries_for_*` helpers, and the third parameter of
`async_remove_config_entry_device`, a DeviceEntry by contract). Fires only on
proven receivers; `hass.config_entries` structurally cannot match.

Design point worth keeping: the binder is data-driven. The engine knows nothing
about the device registry; the rule's `match` object carries `module`,
`registry_factory`, `registry_types`, `entry_types`, `entry_methods`,
`entry_functions` and `entry_params`, so a future registry deprecation reuses
the type without an engine change.

Compatibility was the hard constraint: 1.4.1 installs vendor the old engine and
read the same index. A NEW matcher type is silently skipped there
(`_DISPATCH.get` returns None, `Rule.matchable` is False), whereas a new key on
`attr_access` would have made every old install fire on every
`hass.config_entries` in the world. `tests/test_typed_receiver.py` pins the
old-engine behaviour with a simulated 1.4.1 dispatch table.

VERIFIED (all by hand on this machine, 2026-08-17):
* pytest 232 passed, 3 skipped (was 226/3). The matcher's own file is five
  tests; the shape coverage lives in the two fixture trees, which is how
  `test_scanner.py` already works.
* Test housekeeping in the same pass. `test_local_scan.py` was 634 lines
  covering two concerns, so the local/index merge moved to `test_local_merge.py`
  and the scan itself stayed put (342 and 259 lines). `sample_index` had been
  copy-pasted into five files and `FakeCoordinator` into two; both now live in
  `conftest.py` once, along with the local-scan helpers the two files share.
  An audit of all sixteen test files for the usual tells found only two dead
  fixture parameters, both removed. The rest of the suite was left alone: its
  tests map to real incidents, and rewriting them would be churn with
  regression risk.
* Phase 0 re-verified live: the deprecation post (2027.8 window), core dev
  `device_registry.py` signatures, and the published index (3 088 scanned,
  623 affected, sibling repos_hit 340/216/70/17/0 exactly as expected).
  One drift found: `async_entries_for_config_subentry` no longer exists at
  module level on core dev. It stays in `entry_functions` (harmless: a call
  must still resolve to the device_registry module to bind) for code written
  against older cores.
* Real-repo validation: rescanned 142 HACS integrations that hit the sibling
  device-registry rules with `tools/scan.py --force --only`. 57 findings in 27
  repos, all hand-checked against the repository source at the scanned tag,
  57/57 genuine, zero `hass.config_entries`. Measurement also added
  `async_get` (the `DeviceRegistry.async_get(device_id)` method form) to
  `entry_methods`, which nearly half the true positives use.
* Adversarial corpus, added during review: home-assistant/core itself, 9 865
  files and 5 963 textual `.config_entries` occurrences. 51 findings, every one
  a device entry, covering 51 of the 54 device-entry reads present. The three
  misses are handed a device by another file, which one file cannot follow.
* Review found and fixed a real false-positive vector: bindings were module-
  wide, so a name proved in one function leaked into an unrelated function
  reusing the spelling (`device` from a dict was flagged). Binding is per scope
  now, with inheritance into nested scopes and parameter shadowing. Three
  shapes were then added back on evidence: comprehension generators (core's
  `zwave_js`), and a registry kept on an attribute, proved class-wide because
  it is assigned in `__init__` and read elsewhere (core's `search`).
* `build_index.py` validates schema 1 with the new match object published.
* repos_hit lands at 27 on the verification slice. Higher than the sibling
  `primary_config_entry`'s 17 because `.config_entries` is the old,
  long-standing API; precision is what was verified, and it is 57/57.

`state/`, `data/findings.json` and `docs/` were deliberately left to the daily
crawl: ENGINE_VERSION 5 changes the rules hash, so the 03:17 UTC job rescans
the whole catalogue and republishes the index (same as the 1.3.1 rollout).

The brief mentioned PyPI as a target registry; the package has never been on
PyPI (404) and no publish workflow exists, so distribution stays GitHub + HACS,
unchanged.

## v1.4.1 — RSS feed of announced removals (2026-08-13)

From issue #8, asked for on the announcement thread by u/ALERTua: following the
project meant polling index.json and diffing it. The crawl now writes
`docs/feed.xml` beside the index, one item per announced removal, and the board
carries a rel=alternate link. Live and verified:
<https://booyaka101.github.io/hass-breakage-radar/feed.xml> (31 KB, 60 items).

`state/feed.json` records when each rule was first published, because a rule
carries the release that removes it, not the day it was announced. Without it
every item would look new on each rebuild and re-notify every subscriber.

Reviewing the generated feed as a subscriber receives it found two things the
tests passed over, both fixed in PR #10 after PR #9 had already merged:
* `html.escape` is the wrong escaper for XML. It turned every apostrophe into
  `&#x27;`, unreadable in a reader showing raw text. Use
  `xml.sax.saxutils.escape`.
* Rules with `kind: prose` carry a sentence where others carry a symbol, so
  seven titles ran to 91 characters, and an id fallback produced slugs like
  `2027.2: core-prose-sets-an-invalid-entity-id...`. Titles are capped at 72
  and cut on a word boundary.

Deliberately not built: per-integration feeds. That is a few hundred static
files or a server, and the Home Assistant integration already answers "does
this affect me" without polling.

SHIPPED: PRs #9 and #10, released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.4.1>.
226 tests. Publishing only, so the integration is unchanged from 1.4.0.

Process note from the owner this session: slow down on releases. Four went out
on 2026-08-12, which is four update prompts for every installed user. Batch
work into fewer, considered releases. Also: #9 was merged while the review was
still in progress, so the fixes landed after the merge and needed a follow-up
PR. If a PR is merged mid-review, expect a follow-up rather than assuming the
branch state is what shipped.

## v1.4.0 — upstream issue lookup (2026-08-12)

The crawler now asks each affected repository what it already says about the
deprecation and publishes it as an optional `upstream` field. Notifications
say: already reported and open (react to it), reported and closed (a fix may
have shipped), archived (plan a replacement), issues disabled (nowhere to
report), or nothing found (link the search).

Design decisions worth keeping:
* **The lookup belongs in the crawler.** GitHub's search API allows 30 requests
  a minute whether authenticated or not, so doing it per user would need a
  token from each of them and break the no-account promise. It rides the daily
  slice, so upstream coverage grows with the scan.
* **A raw search hit is not evidence.** A symbol search matches tracebacks
  pasted into unrelated reports; klejejs/ha-thermia returns "Bug: Everything is
  unavailable" for `async_extract_entity_ids`. The title must name the symbol
  or use removal language. Verified: three real reports pass, that one fails.
* **The index stays schema 1.** `validate_index` rejects anything else, so
  bumping would have broken every installed version to add a nicety.

No-breakage check before merging, since that was the condition: loaded the
shipped v1.3.1 modules from git and ran them against an index carrying
`upstream`. validate_index OK, report byte-identical to the same index without
the field. Board and index rebuilt with real annotated data: board 437 KB,
index did not balloon.

SHIPPED via PR #7, merged as `8360e82`, all nine check-runs green, released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.4.0>.
214 tests. The next daily crawl starts populating `upstream`.

## v1.3.1 — verified the rule carrying 328 integrations (2026-08-12)

`device-registry-async-get-device` accounts for 328 of 593 affected
integrations and nearly all of the 2027.8 deadline, at medium confidence.
Hand-checked 45 of its findings against real source in two samples: 44 genuine.
The 11.3% catalogue hit rate is legitimate, so the 2027.8 cliff is real.

The one false positive awaited its own API client's method of the same name.
Core defines the registry method as a plain `def` taking identifiers or
connections, so an awaited call cannot be it. Added an opt-in `not_awaited`
matcher constraint; 36 of 36 sampled true positives unchanged, false positive
gone. ENGINE_VERSION 4 forces the re-crawl.

SHIPPED via PR #6, merged as `c7b3230`, all nine check-runs green, released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.3.1>.

Two process failures worth remembering, both caught before merge:
* The first verification run reported three true positives lost. That was the
  harness, not the fix: Python 3.11 cannot parse PEP 695 generics, so the files
  never parsed at all. LESSONS.md already records this; use
  `.cache/py314/python.exe` for anything that parses other projects' code.
* Regenerating `data/rules.json` with `blog_rules.py --no-network` silently
  dropped 15 blog-derived rules (142 -> 127). The test suite passed throughout
  because those rules are informational. Always diff the regenerated rule set
  against the previous one, and run the generator with the network unless you
  mean to drop them.

## v1.3.0 — see what breaks when, and actionable notifications (2026-08-12)

From issue #3 (DunLaoghaire1): "I'd like to see when an integration will
break. Not just that 13 will break." Shipped via PR #4, squash merged.

* Dated schedule in the summary card and on the sensor; releases sort
  numerically; dates phrased in words.
* Alert window configurable 30 days to a year via an options flow, applied
  without a restart. Capped at 5 individual notifications whatever the window.
* Notifications link to releases, to a search for an existing report, and to
  the core change. Deliberately NOT to a blank issue form: a popular
  integration has thousands of users and duplicate issues would make this tool
  a burden on volunteer maintainers. Verified on real repos, one already had
  two closed issues about the exact deprecation we flag.
* Sensor attributes were 19 KB against HA's 16 KB recorder limit, so nothing
  was being recorded. Now 7 KB; the full report moved to diagnostics.py.

**Testing method that paid for itself:** ran the branch in a real Home
Assistant container (docker, port 8124) against the live index with nine
genuinely affected integrations installed. That is what surfaced the recorder
limit, a code block that would not wrap, a wrong entity id in card copy, and
the duplicate-issue risk. None of it was visible from unit tests.
Reproduce with `.cache/seed_test_config.py` then the docker run in that file's
header; `.cache/ha_onboard.py` completes onboarding via the API.

Attribute renames (breaking for templates): details -> findings,
by_release -> schedule, not_in_index -> not_analysed, clean_domains ->
clean_count. HACS minimum raised to 2024.11 for OptionsFlow.config_entry.

VERIFIED: 196 passed, 3 skipped. All nine check-runs green on `5ca7208`.
Released <https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.3.0>.
Issue #3 left OPEN pending the reporter (it auto-closed on merge because the
squash body said "Closes #3"; reopened deliberately).

Next candidate, needs owner sign-off: move the existing-issue lookup into the
crawler (it has a token and 5000 req/hr) and publish per integration whether a
report exists, its state, whether issues are enabled, and the repo's issue
templates. The card could then say "already reported and closed, update" or
"open report, add a reaction" or offer a prefilled draft in the repo's own
template format. Doing it on the user's box is wrong: unauthenticated GitHub
search is ~10 req/min and asking for a token breaks the no-account promise.

## v1.2.2 — setup no longer waits for the local scan (2026-08-12)

First real user bug report: issue #1 (DunLaoghaire1, HA 2026.8.1), config
entry setup cancelled mid-scan. Root cause was structural and mine: since
1.1.0 the full local scan ran inside the coordinator's first update, which
setup waits on, so a slow box could hang in setup long enough to be cancelled.

Fix (PR #2, first change to go through the PR route now that there are real
users): first update does discovery only and returns an index-only report;
the scan runs as a background task and publishes via async_set_updated_data
when it lands. Same on every refresh; mtime cache keeps repeats cheap. Five
coordinator tests, one of which fails if the scan ever runs inside
_async_update_data again.

SHIPPED: merged as `892a973` (rebase, branch deleted), all nine check-runs
green, released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.2.2>.
Issue #1 left OPEN pending the reporter's confirmation; asked for their
integration count and hardware to learn real scan durations. Owner direction
now standing: user-facing changes go via PR, and outward-facing prose
(issues, release notes) in plain human tone, no em dashes.

## v1.2.1 — the release-date estimate was wrong, and so was my claim (2026-08-12)

The owner challenged the 1.2.0 claim "Home Assistant publishes release
*numbers*, not dates". Checked properly: the release FAQ
(home-assistant.io/faq/release/) publishes the schedule — **first Wednesday of
every month** — and that rule predicted all eight 2026 releases to the day,
while the shipped 1st-of-month estimate accumulated 23 days of error over the
same eight. Estimator corrected, eight tests pin it against the real 2026
release dates, claim fixed in README/const/issue text. `broken_now` never used
the estimate and was unaffected.

SHIPPED: `7db31fb`, all nine check-runs green, pytest **154 passed, 3
skipped**, released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.2.1>.
Lesson for future sessions: the "publishes no dates" claim came from checking
the developer docs (404) and one blog post, then generalising; the FAQ had the
schedule all along. Verify a negative claim against the obvious FAQ before
shipping it in user-facing text.

## v1.2.0 — urgency levels, self-scanning, module split (2026-08-11)

Driven by two pieces of real user feedback on the r/homeassistant thread.

**Levels.** A deadline a year out and one three weeks out were presented
identically. Now `broken_now` / `imminent` / `upcoming`, where urgency decides
presentation: an ERROR card each for broken, a WARNING card each for imminent,
one grouped summary for the rest. The 30-day window comes from HA's monthly
cadence (ships in the first week; the estimate uses the 1st, so it is up to six
days early and never late). **`broken_now` is decided by version comparison
alone**, never the date estimate, so that half stays exact.

**We no longer exempt ourselves.** The local scan skipped `breakage_radar`
while `tools/check_local.py` never did — the two disagreed, and a tool that
exempts itself from its own check is a check nobody tests. Now scanned and
reported like anything else, with `test_our_own_shipped_component_is_clean`
running the shipped rules over the shipped component so CI fails if we ever use
a doomed API.

**`report.py` split** (509 lines, four responsibilities) into `discovery.py` /
`scanner.py` / `report.py`. No behaviour change, no compatibility aliases.

VERIFIED (2026-08-11): pytest **146 passed, 3 skipped** on 3.11 and 3.12
(was 123); all nine check-runs green on `9800d8b`; released as
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.2.0>.
Levels verified end to end against the live index across four simulated dates
(263 days out → upcoming; 21 days → imminent; after upgrading to 2027.5 →
broken_now), with `breakage_radar` itself appearing in `clean_domains`.

Open question left for the owner: whether to extract the vendored 556-line
`rules_engine.py` to a PyPI package (the idiomatic HA pattern — integration as
a thin wrapper over a library) or keep vendoring it with the byte-identity test.
Kept vendored for now; `"requirements": []` is a real benefit.

## v1.1.1 — correctness fixes for 1.1.0, found by auditing it (2026-08-11)

Four defects, three of them "user is told everything is fine when it is not":

1. **False all-clear on upgrade** — the consumer reused the crawler's
   future-only rule filter, so upgrading onto the breaking release flipped an
   affected domain to `clean`. Now every matchable rule applies regardless of
   tense and findings are classified `upcoming` / `broken_now` (new `when`
   detail key, `broken_now` sensor attribute, per-integration ERROR Repairs
   issues + aggregate escalation).
2. **Forks lost their verdict** — scan keyed by directory name, merge looked up
   manifest domain; the fork case 1.1.0 was built for silently dropped its
   findings. Now keyed by manifest domain (paths still show the real directory).
3. **Zero-matcher index laundered everything clean** — a scan with no rules now
   yields `unknown` with a reason and never overrides an index finding.
4. **Symlinked component directories skipped** — discover counted them
   installed, scan skipped them. Top-level links are followed now; symlinked
   subdirectories still are not. (Fix verified by test on Linux CI; Windows
   here cannot create POSIX symlinks without privileges.)

Also: release comparisons in `by_release` / `earliest_release` are numeric now
(`2027.10` no longer sorts before `2027.5`).

VERIFIED (2026-08-11): `python -m pytest` **113 passed, 3 skipped** (was 102/2;
+11 regression tests; skips = 2 opt-in network + symlink-on-Windows); the three
defect-demonstration scripts in `.cache/` all flip to ok; live-index validation
still **15/15 line-for-line** under Python 3.14; E2E vs the live index
unchanged (state 2, 3 local details, lookalike clean).

Origin note: defect 1 was found while evaluating a user feature request
(WannaBMonkey on the announcement thread asking for per-integration Repairs
issues) — the tiered `broken_now` issues are also the honest answer to that
request, without shipping 8 unfixable year-ahead cards.

SHIPPED (2026-08-11): pushed as `70a45ad`, Validate green on all six jobs
(Linux pytest = 114 passed / 2 skipped — the symlink regression test *ran*
there), tag `v1.1.1`, release
<https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.1.1>
created after the green run.

## v1.1.0 — the integration now scans the user's own code (2026-08-11)

The gap this closes: the integration only looked installed domains up in the
published index, so forks, renames, non-HACS installs and the ~588 catalogued
integrations the daily crawl had not reached got no verdict beyond
`not_in_index`. The engine is now vendored into the integration
(`custom_components/breakage_radar/rules_engine.py`, byte-identical to
`tools/rules_engine.py`, guarded by a test) and runs over the installed bytes.

### VERIFIED working (all run by hand on this machine, 2026-08-11)

| Check | Result |
|---|---|
| `python -m pytest` | **102 passed, 2 skipped** (baseline was 87 passed, 1 skipped; +15 offline tests, +1 opt-in network test, 1 existing test updated for the new `source` key) |
| True-positive fixture, absent from index | sensor reads **1**, one detail `source: local`, `device_tracker.py:12`, `by_release {"2027.5": ["fixture_tracker"]}`, NOT in `not_in_index` |
| False-positive fixture | zero findings, lands in `clean_domains` |
| `git ls-files '*manifest.json'` | exactly one path |
| Engine byte-identity | `tools/rules_engine.py` == vendored copy |
| Live-index validation (Python 3.14, `.cache/validate_local_scan.py`) | 6 repos at the crawler's exact refs — pycupra, ha_xiaomi_home, vserver-ssh-stats, padavan-tracker, YandexStation, homeconnect_local_hass — **15/15 findings reproduced rule-for-rule, line-for-line** |
| E2E vs the live index (`.cache/e2e_local_scan.py`) | simulated box with real pycupra v0.2.23 + both fixtures → state 2, 3 local details, lookalike clean, `not_in_index` empty |

### Design points worth remembering

* **Merge order:** local affected > local clean > index affected > index clean >
  local unknown (with reason) > unknown. Local verdicts replace index verdicts
  because they describe the installed bytes; a truncated/unparseable local scan
  falls back to the index instead of reading as clean.
* **The local scan parses with the box's Python.** Found live during
  validation: `homeconnect_local_hass` uses PEP 695 `type` aliases, which the
  box's 3.11 cannot parse while the crawler's 3.14 can. The merge handles it
  (domain → unknown → index verdict used); the network test asserts that
  contract, and the strict line-for-line comparison runs under
  `.cache/py314/python.exe`.
* Scan cache key: (file count, newest mtime ns, total size, unreadable dirs,
  rules fingerprint incl. ENGINE_VERSION and current HA version, caps).
* The vendor-marker directories the crawler skips are skipped locally too.

### Shipped (2026-08-11)

| Step | Status |
|---|---|
| Push | ✅ `6af6142` on `main`, rebased onto three daily-crawl commits (index now 2 500 scanned, 508 affected) |
| `Validate` workflow | ✅ green on the release commit — hassfest, index schema, HACS validation, pytest 3.12/3.13/3.14 |
| Tag | ✅ `v1.1.0` (annotated), pushed |
| Release | ✅ <https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.1.0> — created **after** the green run (the ordering hacs/default's PR template checks), with real notes from the CHANGELOG |
| Announcement | ✅ r/homeassistant, flair "Show & Tell": <https://www.reddit.com/r/homeassistant/comments/1vlijul/508_of_the_2500_hacs_integrations_scanned_so_far/> — verified publicly visible (`removed_by_category: null`) with markdown rendering intact; leads with the board link per the README's distribution plan |

### Remaining (not blocking, not mine to do)

* hacs/default#9839 still awaits maintainer review — unchanged by this release.

## Where things stand

**v1 is complete and verified end to end.** Both artifacts work against real data:
the crawler produces a real index from a real crawl of the live HACS catalogue, and
the Home Assistant integration consumes that index and reports on a local box.

Nothing in the repository is mocked, stubbed or placeholder. Fixture data exists only
under `tests/fixtures/`.

## What is VERIFIED working

Each of these was run by hand on this machine, against the live internet:

| Step | Command | Verified result |
|------|---------|-----------------|
| Rule extraction | `python tools/extract_rules.py` | 9 865 core files scanned, 145 deprecation call sites, **114 rules, 23 matchable + future** |
| Prose + curated rules | `python tools/blog_rules.py` | merges 10 hand-curated matchable rules and blog-derived informational rules |
| Catalogue | `python tools/catalog.py` | **3 088** integrations from `data-v2.hacs.xyz`; fallback to `hacs/default` unit-tested |
| Crawl | `python tools/scan.py --limit 1300` | 1 349 s: **1 295 scanned, 3 unreachable, 2 error, 270 with findings, 659 findings** |
| Index | `python tools/build_index.py` | `docs/index.json` (310 KB) validates against schema 1; `docs/index.html` (207 KB) renders 5 populated release sections |
| Board UI | driven in Chrome over CDP | table renders 283 rows across 5 sections; text filter, confidence filter and column sort all verified working; screenshot captured |
| Sensor E2E | shipped `report.py` vs shipped `docs/index.json` | state **2**, `by_release {"2027.5": ["pycupra"], "2027.7": ["xiaomi_home"], "2027.8": ["pycupra","xiaomi_home"]}` on a simulated box |
| Tests | `python -m pytest` | **85 passed, 1 skipped** (the skip is the opt-in `--run-network` test) |
| Clean install | `pip install .` in a fresh venv | entry points resolve and `breakage-radar-catalog` produced a real 3 088-entry catalogue from an empty directory |

## Two things learned during the build that shaped the design

1. **Home Assistant `dev` needs Python 3.14 to parse.** On 3.11, 23 core files fail
   `ast.parse`; on 3.12, 11 still do (PEP 758 `except A, B:`); on 3.14, zero. Running
   the extractor on 3.12 silently loses ~40 % of the call sites, including the ones in
   `device_registry.py` and `device_tracker/legacy.py`. CI pins 3.14 for the crawl and
   `rules.json` records both the interpreter used and every file it could not parse.

2. **A rule written from a spec is a hypothesis — measurement caught two.**
   (a) The first 25-repo slice produced a real false positive: `0xAlon/dolphin` calls
   `entity.async_generate_entity_id`, but the deprecated function is
   `entity_registry.async_generate_entity_id` — same name, different module. Matchers
   now resolve imports before firing.
   (b) `@deprecated_hass_argument` deprecates the leading `hass` **argument**, not the
   function. `AlexxIT/YandexStation` calls `service.async_extract_entity_ids(call)` and
   `service.async_extract_entity_ids(hass, call)` on consecutive lines — one healthy,
   one not. A dedicated `call_hass_argument` matcher now discriminates, and the rule
   `core-call-verify-domain-control` dropped from 1 hit to 0 as a result.
   Both cases are pinned in `tests/test_scanner.py`. `ENGINE_VERSION` is folded into the
   crawl's `rules_hash`, so any change to matching semantics forces a full rescan rather
   than leaving stale findings behind — which is why the shipped index came from a clean
   1 300-repo crawl after the second fix, not from the earlier runs.

## Published — all shipping steps DONE (2026-08-08)

| Step | Status |
|------|--------|
| Public repo | ✅ <https://github.com/Booyaka101/hass-breakage-radar> — MIT, 8 topics, description + homepage |
| GitHub Pages from `main` `/docs` | ✅ live, HTTP 200 — <https://booyaka101.github.io/hass-breakage-radar/> |
| Published index | ✅ <https://booyaka101.github.io/hass-breakage-radar/index.json> serving schema 1 |
| `Validate` workflow | ✅ green — pytest 3.12/3.13/3.14, hassfest, HACS validation, index schema |
| Release | ✅ [v1.0.1](https://github.com/Booyaka101/hass-breakage-radar/releases/tag/v1.0.1) (v1.0.0 also tagged) |
| `hacs/default` PR | ✅ **[hacs/default#9839](https://github.com/hacs/default/pull/9839)** — open, MERGEABLE, 1-line diff, all 9 HACS checks green. Awaiting maintainer review. |

### Three problems only publishing could surface

1. **`aiohttp` was never stubbed.** `tests/conftest.py` stood in for every
   `homeassistant` symbol but not `aiohttp`, which is installed on the build machine
   and absent on a bare runner. All three pytest jobs failed at collection. Fixed in
   the first CI commit.
2. **HACS requires brand assets.** 8/9 HACS checks passed immediately; the miss was
   brands. A 256×256 radar icon/logo is now generated (stdlib `zlib`+`struct`) at
   `custom_components/breakage_radar/brand/`.
3. **`hacs/default` rejects a second `manifest.json` anywhere in the repo.** Their
   `scripts/helpers/integration_path.py` walks the *whole* clone for `*manifest.json`
   and exits if it does not find exactly one. The two scanner fixtures each shipped
   one, so the PR's Hassfest job died in 37 ms with a bare `exit code 1` — invisible
   from our own hassfest, which passes because it is pointed at one path. Fixture
   manifests are now built in `tmp_path`, and
   `test_manifest_json_is_unique_in_the_repository` guards the invariant. This is what
   v1.0.1 exists for.

### Remaining (not blocking, not mine to do)

* Wait for a `hacs/default` maintainer to merge #9839 — after that Breakage Radar
  appears in every HACS install with no "custom repository" step.
* The daily `Crawl` workflow fires at 03:17 UTC and will widen coverage past the
  1 300 repositories in the shipped index on its own.

## Known limitations carried into v1 (documented in README, not hidden)

* Matching in the Home Assistant integration is **by domain**, so a locally forked or
  renamed copy of a HACS integration is reported as "not in index" rather than
  analysed. The sensor exposes `not_in_index` so this is visible, and `details` carries
  both `scanned_version` and `installed_version` so a version mismatch is obvious.
* `DeviceEntry.config_entries` ships as an informational rule with no matcher, because
  `config_entries` is also the ubiquitous `hass.config_entries` and a static matcher
  would fire on nearly every integration.
* Coverage grows over time: one crawl slice is capped so the daily job stays well
  inside GitHub's rate limits. `docs/index.json` `coverage.repos_scanned` always states
  honestly how much of the 3 088-repo catalogue has been visited.
