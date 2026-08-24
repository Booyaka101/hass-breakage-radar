# Changelog

All notable changes to Breakage Radar. Versions follow
[semver](https://semver.org/); the `custom_components/breakage_radar/manifest.json`
and `pyproject.toml` versions always agree (enforced by a test).

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
