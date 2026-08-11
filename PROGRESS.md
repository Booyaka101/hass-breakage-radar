# PROGRESS — hass-breakage-radar

Status at the end of the v1.1.1 build session (2026-08-11); earlier sections are
kept as history.

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
