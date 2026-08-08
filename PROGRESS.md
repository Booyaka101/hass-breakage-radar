# PROGRESS — hass-breakage-radar

Status at the end of the v1 build session (2026-08-08).

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
