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

## Exact next steps (for the owner, from the phone)

1. `git init && git add -A && git commit -m "feat: Breakage Radar v1.0.0"`.
2. `gh repo create Booyaka101/hass-breakage-radar --public --source=. --push`.
3. Repository **Settings → Pages → Source: Deploy from a branch → `main` / `/docs`**.
   The board then lives at `https://booyaka101.github.io/hass-breakage-radar/`.
4. Confirm the `Validate` workflow is green (pytest × 3, hassfest, HACS, index schema).
5. `gh release create v1.0.0 --generate-notes`.
6. Open the PR to `hacs/default` adding `Booyaka101/hass-breakage-radar` to the
   `integration` list. (The agent must not publish, so this is the owner's step.)

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
