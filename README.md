# Breakage Radar for Home Assistant

**Which of my custom integrations stop working, and in which Home Assistant release?**

Home Assistant announces API removals a year ahead on the developer blog and marks
them in code with `breaks_in_ha_version=`. Core integrations get migrated. Custom
integrations mostly do not — and the author of the integration you installed from HACS
two years ago may not be reading the developer blog at all.

Worse, the warning never reaches *you*. In `homeassistant/helpers/frame.py`:

```python
def report_usage(
    what: str,
    *,
    breaks_in_ha_version: str | None = None,
    core_behavior: ReportBehavior = ReportBehavior.ERROR,
    core_integration_behavior: ReportBehavior = ReportBehavior.LOG,
    custom_integration_behavior: ReportBehavior = ReportBehavior.LOG,
    ...
) -> None:
```

`custom_integration_behavior` defaults to **LOG**. A custom integration that uses a
doomed API gets one line in `home-assistant.log` — while it still works. On upgrade
day it simply stops. And Repairs deliberately will not carry the warning; from the
architecture discussion that approved the legacy device tracker removal
([#1375](https://github.com/home-assistant/architecture/discussions/1375)):

> "Repairs must be user actionable, and in this case, they can't solve it." — Frenck

Breakage Radar closes that gap. It reads the removals out of Home Assistant's own
source, crawls every custom integration in the HACS catalogue for them, publishes the
result as a public index, and ships a Home Assistant integration that tells you which
of **your** installed integrations are on the list and when they die.

📊 **Board:** <https://booyaka101.github.io/hass-breakage-radar/>
🤖 **Index:** <https://booyaka101.github.io/hass-breakage-radar/index.json> (schema 1)

**In the published index right now:** 2 500 of the 3 088 HACS custom integrations
crawled (18 unreachable), **508 affected**, **1 259 findings**, across 5 Home Assistant
releases — 7 in 2026.10, 71 in 2026.11, 9 in 2027.5, 28 in 2027.7 and 418 in 2027.8
(counted by distinct integration domain). Every number comes from a real crawl; nothing
is seeded or simulated. The daily job widens coverage on its own, so the index is
usually ahead of these figures — `coverage` in `index.json` is always authoritative.

---

## Two halves in one repository

| | What it is | Where it runs |
|---|---|---|
| **A. The crawler** | `tools/` — extracts rules from Home Assistant core, fetches the HACS catalogue, scans each repository's `custom_components/**/*.py`, publishes `docs/index.json` + a static board | GitHub Actions, daily |
| **B. The integration** | `custom_components/breakage_radar/` — downloads the index every 12 h, matches it against what is installed, **runs the same matchers over your own installed source**, exposes one sensor and raises a repairs issue | Your Home Assistant box |

No server, no account, no API key, no third-party runtime dependency. The integration's
`manifest.json` declares `"requirements": []`.

---

## Install (Home Assistant side)

### Via HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**
2. URL `https://github.com/Booyaka101/hass-breakage-radar`, category **Integration**
3. Install **Breakage Radar**, restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Breakage Radar** → Submit

### Manually

Copy `custom_components/breakage_radar/` into your Home Assistant `config/custom_components/`
directory and restart, then add the integration from the UI.

The config flow has a single confirmation step — there is nothing to configure.

### What you get

`sensor.breakage_radar_affected` — the number of installed custom integrations with at
least one finding.

Findings come from two sources, and every `details` entry says which:

* `source: local` — the integration parsed the **installed bytes** in your own
  `custom_components/` directory with the same eight AST matchers the crawler
  uses. Forks, renamed copies and integrations installed outside HACS get a real
  verdict this way, and there is no version skew: the scanned version *is* the
  installed version.
* `source: index` — the published index's verdict, used where the local scan
  could not reach one (for example a file over the size caps).

```yaml
state: 2
attributes:
  by_release:
    "2027.5": [some_tracker]
    "2027.8": [some_tracker, another_integration]
  details:
    - domain: some_tracker
      rule_id: legacy-device-tracker-platform
      breaks_in: "2027.5"
      file: custom_components/some_tracker/device_tracker.py
      line: 12
      confidence: high
      source: local
      when: upcoming
      repository: someone/some-tracker
      scanned_version: "1.3.2"
      installed_version: "1.3.2"
      message: "Implements the legacy (non-config-entry) device tracker platform API..."
      learn_more: https://developers.home-assistant.io/blog/2026/04/20/legacy-device-tracker-deprecation/
  index_generated_utc: "2026-08-11T03:57:51Z"
  affected_domains: [some_tracker, another_integration]
  broken_now: {}
  broken_now_count: 0
  not_in_index: [broken_thing]
  not_in_index_reasons:
    broken_thing: "1 of 3 Python file(s) could not be parsed"
  files_scanned: 412
  unparsed_files: 1
  skipped_files: 0
  earliest_release: "2027.5"
  total_findings: 5
```

A domain that is nowhere in the index but parses clean locally lands in
`clean_domains`; only a domain **neither** side could analyse stays in
`not_in_index`, with the reason alongside. `files_scanned`, `unparsed_files` and
`skipped_files` make a truncated scan visible — it is never silently reported
as clean. The scan runs in an executor and is cached on each integration's file
count, newest mtime, total size and the rules fingerprint, so the 12-hourly
refresh re-parses nothing that has not changed.

Every finding is also classified against the Home Assistant version you are running:
`when: upcoming` while the deadline is ahead, `when: broken_now` once it has arrived.
Upgrading past a deadline makes a finding *more* visible, never less — the domain
stays affected and moves into the `broken_now` attribute.

When the count is above zero a **Repairs** issue appears, naming the integrations and
the first deadline. It is deliberately *not* fixable in place — the code lives in
someone else's repository — but it links to the board and clears itself when the count
returns to zero. Integrations in `broken_now` additionally get **one ERROR-severity
Repairs issue each**: unlike the year-ahead aggregate, "this integration is failing on
this system right now — update or replace it today" is individually actionable, and
each issue clears the moment an updated version no longer contains the removed API.

Automate on it:

```yaml
automation:
  - alias: Warn me about doomed custom integrations
    triggers:
      - trigger: numeric_state
        entity_id: sensor.breakage_radar_affected
        above: 0
    actions:
      - action: notify.persistent_notification
        data:
          title: "{{ states('sensor.breakage_radar_affected') }} integrations will break"
          message: >-
            First deadline: Home Assistant
            {{ state_attr('sensor.breakage_radar_affected', 'earliest_release') }}.
            {{ state_attr('sensor.breakage_radar_affected', 'affected_domains') | join(', ') }}
```

---

## Run the crawler yourself

Python 3.12+ (3.14 strongly recommended — see *Which Python* below). No dependencies.

```bash
git clone https://github.com/Booyaka101/hass-breakage-radar
cd hass-breakage-radar

python tools/extract_rules.py     # -> data/rules.json      (from HA core source)
python tools/blog_rules.py        # -> merges blog + data/manual_rules.json
python tools/catalog.py           # -> data/catalog.json    (every HACS integration)
python tools/scan.py --limit 400  # -> data/findings.json + state/crawl.json
python tools/build_index.py       # -> docs/index.json + docs/index.html
```

Run them in that order. `extract_rules.py` **rewrites** `data/rules.json` from core alone,
so `blog_rules.py` must follow it to merge the hand-curated and prose rules back in.
The GitHub Actions workflow does exactly this.

Real output from `tools/extract_rules.py` on this machine:

```
INFO breakage_radar.tools: core version in tarball: 2026.9 (sha256 3b8456c44b40)
INFO breakage_radar.tools: scanned 9865 core files, 145 deprecation call sites
INFO breakage_radar.tools: wrote data/rules.json: 114 rules (62 future, 23 matchable)
INFO breakage_radar.tools:   2026.11    async_import_statistics(missing unit_class)
INFO breakage_radar.tools:   2027.1     async_register_info
INFO breakage_radar.tools:   2027.2     async_generate_entity_id
INFO breakage_radar.tools:   2027.6     FlowHandler.show_advanced_options
INFO breakage_radar.tools:   2027.8     async_device_info_to_link_from_entity
INFO breakage_radar.tools:   2027.8     async_remove_stale_devices_links_keep_entity_device
```

Real output from `tools/scan.py` and `tools/build_index.py`. This is the v1.0.0 crawl
slice, kept as a worked example of what a run prints — the published index has since
been widened by the daily job, so its totals are larger (see the figures at the top):

```
INFO breakage_radar.tools: 32 matchable rules (core 2026.9, rules_hash 0708f404a48c72d3)
INFO breakage_radar.tools: 3088/3088 repositories need a scan; this slice takes 1300
INFO breakage_radar.tools: [14/1300] 404GamerNotFound/vserver-ssh-stats  scanned  5 finding(s)  refs/tags/v1.5.6
INFO breakage_radar.tools: slice done in 1349s: scanned=1295, unreachable=3, error=2, with_findings=270, findings=659

INFO breakage_radar.tools: wrote docs/index.json and index.html: 270 affected of 1300 scanned (659 findings, 81 rules)
INFO breakage_radar.tools:   2026.10: 4 integration(s)
INFO breakage_radar.tools:   2026.11: 36 integration(s)
INFO breakage_radar.tools:   2027.5: 6 integration(s)
INFO breakage_radar.tools:   2027.7: 18 integration(s)
INFO breakage_radar.tools:   2027.8: 218 integration(s)
INFO breakage_radar.tools: rule hit-rates (repos hit / repos scanned):
INFO breakage_radar.tools:   device-registry-async-get-device               152 repo(s)   11.7%
INFO breakage_radar.tools:   device-info-via-device                          89 repo(s)    6.8%
INFO breakage_radar.tools:   device-registry-config-entry-mutation-params     34 repo(s)    2.6%
INFO breakage_radar.tools:   device-tracker-battery-level                    12 repo(s)    0.9%
INFO breakage_radar.tools:   legacy-device-tracker-platform                   6 repo(s)    0.5%
```

Real finds from that crawl, each hand-verified against the repository's own source:

| Integration | Breaks in | Why |
|---|---|---|
| `XiaoMi/ha_xiaomi_home` (22 k ★) | 2027.7 | `battery_level` and `location_name` properties on a device tracker entity |
| `WulfgarW/homeassistant-pycupra` | 2027.5 | module-level `async_setup_scanner` in `device_tracker.py` |
| `PaulAnnekov/home-assistant-padavan-tracker` | 2027.5 | `get_scanner` **and** a `DeviceScanner` subclass |
| `404GamerNotFound/vserver-ssh-stats` | 2027.8 | `async_update_device(remove_config_entry_id=…)` and `DeviceInfo(via_device=…)` |

`state/crawl.json` remembers what was scanned at which version, so the next run only
revisits repositories that actually changed. `--limit` caps a run; least-recently-scanned
repositories go first, so coverage rotates on its own.

### Which Python

Home Assistant's `dev` branch tracks the newest CPython syntax. As of core 2026.9 dev
it uses PEP 758 unparenthesized `except A, B:`, which needs **Python 3.14**. Measured
on the same tarball:

| Interpreter | Core files that fail to parse | Deprecation call sites found |
|---|---|---|
| 3.11 | 23 | 98 |
| 3.12 | 11 | 115 |
| **3.14** | **0** | **145** |

Running on an older interpreter silently loses rules from `device_registry.py`,
`device_tracker/legacy.py`, `trigger.py` and `config_entries.py`. The extractor never
hides this: `rules.json` records `extractor_python`, `counts.core_files_unparsed` and
the full `unparsed_core_files` list, and logs a warning. The GitHub Actions workflow
pins 3.14.

---

## How rules are chosen

There are three sources, and they are not equally trusted.

**1. Extracted from core (`origin: core-ast`).** Every call in `homeassistant/**/*.py`
that passes a string literal to `breaks_in_ha_version`. The message is then turned into
a matcher only when it names something specific enough:

* `"calls async_device_info_to_link_from_entity, which is deprecated…"` → a `call`
  matcher, pinned to the module that defines it.
* `"doesn't specify unit_class when calling async_import_statistics"` → a
  `call_missing_kwarg` matcher.
* `"calls `async_listen` which is deprecated"` → **no matcher.** `async_listen` is 12
  characters and everybody has one. It ships as information only.

Auto-derived symbols must be at least 18 characters and survive a denylist. Everything
else is published for the board but never claims a repository is affected.

**2. Hand-curated (`origin: manual`, `data/manual_rules.json`).** Removals announced in
prose with no `report_usage` call behind them — the legacy device tracker platform API,
the device registry single-config-entry changes, the device tracker property removals.
Each one quotes its source post.

**3. Blog prose (`origin: blog`).** Every removal sentence found on
<https://developers.home-assistant.io/blog/>, published as `matchable: false` so the
board shows the deadline even when no static check exists.

### Why matching resolves imports

A rule written from a spec is a hypothesis, and this one was wrong on the first crawl
slice. `entity_registry.async_generate_entity_id` is removed in 2027.2;
`entity.async_generate_entity_id` is fine. They share a name. A naive matcher flagged
`0xAlon/dolphin`, which imports the healthy one.

So every `call` matcher can be pinned to a module, and the engine builds an import map
before it fires:

```python
from homeassistant.helpers.entity import async_generate_entity_id           # no finding
from homeassistant.helpers.entity_registry import async_generate_entity_id  # finding
from homeassistant.helpers import entity_registry as er                     # finding
from .my_own_registry import async_get_device                               # no finding
```

Where the receiver is only known at runtime — `registry.async_get_device(...)` — the
import graph cannot prove anything, so those rules opt in explicitly with
`allow_unresolved_attribute` and are published at `confidence: medium`.

`docs/index.json` publishes every rule's measured `repos_hit`, so a rule that fires on
an implausible fraction of the catalogue is visible rather than quietly taxing everyone.

### Matcher types

| Type | Fires on |
|---|---|
| `moduledef` | a module-level `def`/`async def` with one of `names` |
| `classbase` | a `class` deriving from one of `bases` |
| `attr` | a property or `_attr_` assignment named in `names` |
| `attr_access` | reading `something.<name>` |
| `call` | a call to one of `names` |
| `call_kwarg` | a call to one of `names` passing any keyword in `kwargs` |
| `call_missing_kwarg` | a call to one of `names` *not* passing `kwarg` |
| `call_hass_argument` | a call to one of `names` that passes `hass` — for `@deprecated_hass_argument`, where the *argument* is deprecated, not the function |

Any matcher can be narrowed with `files` (exact basenames); `attr` matchers can also
require `in_class_base`.

The engine lives in `tools/rules_engine.py` and is vendored byte-for-byte at
`custom_components/breakage_radar/rules_engine.py`, so the crawler and the
integration's local scan can never disagree about the same source — a test
asserts the two copies are identical.

---

## The worked example

A custom integration on the legacy device tracker platform API:

```python
# custom_components/fixture_tracker/device_tracker.py
"""Fixture: a custom integration still on the legacy device tracker platform API.

A module-level ``setup_scanner`` in a file named ``device_tracker.py`` is the
legacy platform entry point Home Assistant removes in the 2027.5 release.
"""

from homeassistant.const import CONF_HOST

DOMAIN = "fixture_tracker"


def setup_scanner(hass, config, see, discovery_info=None):   # <- line 12
    ...
```

produces **exactly one** finding:

```json
{
  "rule_id": "legacy-device-tracker-platform",
  "breaks_in": "2027.5",
  "file": "custom_components/fixture_tracker/device_tracker.py",
  "line": 12,
  "confidence": "high"
}
```

and with it installed the sensor reads `1` with
`by_release == {"2027.5": ["fixture_tracker"]}`.

Lookalikes produce **zero** findings — `setup_scanner` inside a class body is a method,
and `setup_scanner` in `sensor.py` is just a function with an unlucky name. Both are
pinned in `tests/test_scanner.py`.

The four legacy entry points the rule looks for are not guessed. They are the exact
list core dispatches on in `homeassistant/components/device_tracker/legacy.py`:

```python
"async_get_scanner",
"get_scanner",
"async_setup_scanner",
"setup_scanner",
```

---

## Index format (schema 1)

```jsonc
{
  "schema": 1,
  "generated_utc": "2026-08-08T12:00:00Z",
  "core_version": "2026.9",
  "coverage": { "catalog_total": 3088, "repos_scanned": 900, "repos_affected": 190, ... },
  "releases": { "2027.5": ["some_tracker"], "2027.8": ["another"] },
  "rules": [
    { "id": "legacy-device-tracker-platform", "breaks_in": "2027.5",
      "message": "...", "source": "https://developers.home-assistant.io/blog/...",
      "confidence": "high", "matchable": true, "hits": 3, "repos_hit": 3,
      "match": { "type": "moduledef",
                 "names": ["async_get_scanner", "get_scanner",
                           "async_setup_scanner", "setup_scanner"],
                 "files": ["device_tracker.py"] } }
  ],
  "integrations": [
    { "full_name": "someone/some-tracker", "domain": "some_tracker",
      "version": "1.4.0", "stargazers_count": 42, "earliest_breaks_in": "2027.5",
      "findings": [ { "rule_id": "...", "breaks_in": "2027.5",
                      "file": "...", "line": 12, "confidence": "high" } ] }
  ],
  "clean_domains": ["..."],
  "unreachable_domains": ["..."]
}
```

`integrations` lists only repositories **with** findings. `clean_domains` lists the ones
scanned and found clean, so a consumer can tell "no problems" from "not looked at yet".

Every `matchable: true` rule ships its matcher as the nested `match` object — that is
what lets the integration run the same rules over locally installed code without the
index changing shape for it.

---

## Configuration

The Home Assistant integration needs none. For the crawler:

| Setting | Where | Default |
|---|---|---|
| Repos per run | `tools/scan.py --limit N` | `400` |
| Rescan everything | `tools/scan.py --force` | off |
| One repository | `tools/scan.py --only owner/repo` | — |
| Politeness pause | `tools/scan.py --sleep 0.25` | `0` |
| Core branch | `tools/extract_rules.py --ref dev` | `dev` |
| Skip the blog crawl | `tools/blog_rules.py --no-network` | off |
| Force the catalogue fallback | `tools/catalog.py --force-fallback` | off |
| Working directory | `BREAKAGE_RADAR_ROOT` env var | the repo checkout |

Index URL and poll interval live in `custom_components/breakage_radar/const.py`
(`INDEX_URL`, `UPDATE_INTERVAL`) if you want to point the integration at your own crawl.

---

## Failure behaviour

Everything below is covered by a test.

| Situation | What happens |
|---|---|
| Repository tag missing | falls back to `v`-tag, then `main`, then `master`; then `status: unreachable`, crawl continues |
| Repository has no `custom_components/` | recorded as scanned with zero findings |
| `SyntaxError` in third-party source | that file is skipped and counted; the run never aborts |
| GitHub returns 429 | exponential backoff (1 s, 2 s, 4 s), then the slice ends cleanly with state committed |
| Non-retryable HTTP status | one clear error line, no traceback |
| Corrupt tarball | `status: error` on that repo, crawl continues |
| Crawl interrupted | state and findings are checkpointed every 25 repositories |
| `index.json` unreachable from HA | the last good report is kept and the sensor goes **unavailable**; `last_error` says why |
| `index.json` is not schema 1 | rejected with a message rather than half-read |
| `custom_components/` missing or unreadable | empty report, no exception |
| An installed `manifest.json` is corrupt | the component still counts as installed, with an empty version |
| An installed integration's file will not parse or decode | counted in `unparsed_files`; the domain stays unknown with a reason, never falsely clean |
| An installed integration exceeds the local scan caps | counted in `skipped_files`; the index verdict is used if there is one |
| The local scan itself fails unexpectedly | logged, and the report falls back to index-only matching |
| Home Assistant is upgraded past a finding's deadline | the finding stays, reclassified `broken_now`, and Repairs escalates to ERROR |
| The index ships no matchable rules | domains scan `unknown` with a reason — an empty rule set never reads as clean |
| An integration directory is renamed or forked | matched by the domain its `manifest.json` declares, not the directory name |
| Very affected system | `details` caps at 100 entries and sets `details_truncated` |

---

## Tests

```bash
python -m pytest              # offline; the whole suite
python -m pytest --run-network  # also hits codeload/GitHub for the live-core test
```

The extractor has a golden test against `tests/fixtures/core_mini.tar.gz` — five real
files copied verbatim out of home-assistant/core `dev`, with the archive's sha256
pinned in the test so the fixture cannot drift silently.

The Home Assistant integration is tested **without installing Home Assistant**:
`tests/conftest.py` registers minimal stand-ins for the handful of symbols the
integration imports, so `tests/test_integration.py` exercises the real shipped
`BreakageRadarSensor`, not a copy of its logic. If Home Assistant *is* installed, the
real package is used instead.

---

## Limitations

* **A finding is static analysis, not a guarantee.** `confidence: medium` rules match a
  method name on an object whose type is only known at runtime.
* **Index coverage is partial by design.** One slice is capped so the daily job stays
  inside GitHub's rate limits; `coverage.repos_scanned` always states how much of the
  3 088-repo catalogue has been visited so far. Since 1.1.0 this matters less on your
  own box: whatever the crawl has not reached, the integration scans locally with the
  same rules.
* **The local scan is bounded.** Per integration it reads at most 400 Python files of
  up to 1 MB each; anything beyond that is counted in `skipped_files` and the domain is
  reported unknown rather than clean.
* **The local scan parses with your box's own Python.** An installed file using syntax
  newer than your interpreter (for example PEP 695 `type` aliases on Python 3.11) is
  counted in `unparsed_files` and the domain falls back to the index verdict — the
  crawler parses with 3.14, so the index side never has this gap.
* **`DeviceEntry.config_entries` is informational only.** `config_entries` is also
  `hass.config_entries`, which every integration touches, so no matcher ships for it.
* **Integrations only.** HACS plugins, themes and AppDaemon apps are out of scope for v1.
* **No automatic fixing.** v1 tells you what breaks and when; it does not rewrite code.

---

## Prior art

[`custom-components/breaking_changes`](https://github.com/custom-components/breaking_changes)
did something adjacent and was archived on 2022-05-28, its own README noting *"At the
time of archiving the integration has not worked in over a year."* It compared installed
components against *published* breaking changes — after the fact. Breakage Radar looks at
removals that have **not happened yet**, from a static analysis of the integration's own
source, so there is time to act.

---

## Changelog

Every release is documented in [CHANGELOG.md](CHANGELOG.md), including what each fix
actually changed about the verdict you see. Releases are tagged on
[GitHub](https://github.com/Booyaka101/hass-breakage-radar/releases).

---

## Questions, findings and false positives

* **[Discussions](https://github.com/Booyaka101/hass-breakage-radar/discussions)** —
  questions, "is this finding right?", and anything you would rather not file as a bug.
* **[Issues](https://github.com/Booyaka101/hass-breakage-radar/issues)** — false
  positives and false negatives especially. A finding names the exact file and line, so
  a report that quotes them is immediately actionable, and a wrong rule is worth fixing
  fast: it is a tax on every user it fires on.

---

## Contributing a rule

Add it to `data/manual_rules.json` with a `source` URL that states the removal release,
then run `python tools/blog_rules.py && python tools/scan.py --limit 50 --force` and
check the hit rate in `python tools/build_index.py` output. A rule that fires on a large
fraction of the catalogue is a tax, not a signal — tighten it or ship it as
`matchable: false`.

---

## License

MIT — see [LICENSE](LICENSE).
