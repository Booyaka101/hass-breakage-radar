# Phase 0 — external resource verification

All checks run live on **2026-08-08** from the build machine before any code was written.
Result: **NOT BLOCKED**. Every input exists, contains what the brief claims, and is free.

| # | Resource | Method | Result |
|---|----------|--------|--------|
| 1 | `https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/helpers/frame.py` | `curl` | HTTP 200, 13 926 bytes. Line 162 is verbatim `def report_usage(what: str, *, breaks_in_ha_version: str \| None = None, core_behavior: ReportBehavior = ReportBehavior.ERROR, core_integration_behavior: ReportBehavior = ReportBehavior.LOG, custom_integration_behavior: ReportBehavior = ReportBehavior.LOG, exclude_integrations: set[str] \| None = None, integration_domain: str \| None = None, level: int = logging.WARNING) -> None:` — signature matches the brief exactly. |
| 2 | `https://codeload.github.com/home-assistant/core/tar.gz/refs/heads/dev` | `curl -L` | HTTP 200, 28 458 322 bytes in 4.6 s. `homeassistant/const.py` gives `MAJOR_VERSION = 2026`, `MINOR_VERSION = 9` → current dev release **2026.9**. |
| 3 | AST survey of the core tarball | stdlib `ast` over every `homeassistant/**/*.py` | **92 files** contain `breaks_in_ha_version`; **31 call sites** pass a *string-literal* version to `report_usage` / `deprecated_function` / `deprecated_class`, plus 52 to `ir.async_create_issue`. Future-dated literals present include `2026.11`, `2027.1`, `2027.2.0`, `2027.3.0`, `2027.5.0`, `2027.6`, `2027.8.0`. Acceptance check 1 is therefore satisfiable from real data. |
| 4 | `https://developers.home-assistant.io/blog/2026/04/20/legacy-device-tracker-deprecation/` (raw markdown from `home-assistant/developers.home-assistant`) | `curl` | HTTP 200. Verbatim: *"The legacy (non-config-entry) device tracker platform API is deprecated and will be removed in the Home Assistant 2027.5 release."* Post also enumerates the legacy core integrations by install count from analytics. |
| 5 | `core-dev/homeassistant/components/device_tracker/legacy.py` | grep | Lines 292–295 list the four legacy platform entrypoints verbatim: `"async_get_scanner"`, `"get_scanner"`, `"async_setup_scanner"`, `"setup_scanner"`; line 991 `class DeviceScanner:`. This is stronger ground truth than the brief's two names and is what the shipped rule uses. |
| 6 | `https://github.com/home-assistant/architecture/discussions/1375` | WebFetch | Frenck verbatim: *"Repairs must be user actionable, and in this case, they can't solve it."* Confirmed: the proposal describes **no** mechanism for notifying custom-integration authors — only core integrations. This is the gap the product fills. |
| 7 | `https://developers.home-assistant.io/blog/` | WebFetch | Live index lists a recurring dated deprecation stream, including `2026/07/21/device-registry-single-config-entry` (removal 2027.8), `2026/06/30/async-initialize-triggers-home-assistant-start-deprecated` (removal 2027.8), `2026/06/15/device-tracker-changes` (stops working 2027.7). |
| 8 | `https://developers.home-assistant.io/blog/2026/06/15/device-tracker-changes` | WebFetch | Verbatim: *"The `battery_level` property has been deprecated in all device tracker base classes, and will stop working in Home Assistant Core 2027.7."* and *"The `location_name` property of `TrackerEntity` has been deprecated, and will stop working in Home Assistant Core 2027.7."* |
| 9 | `https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry` | WebFetch | Confirms 2027.8 removal for `DeviceEntry.config_entries`, `DeviceEntry.config_entries_subentries`, `DeviceEntry.primary_config_entry`, `DeviceRegistry.async_get_device()`, `via_device`, and the `add_config_entry_id` / `remove_config_entry_id` family. |
| 10 | `https://data-v2.hacs.xyz/integration/data.json` | `curl` + `json` | HTTP 200, 2 010 728 bytes, **3 088 entries**, object keyed by numeric repo id. Verified entry: `dave-code-ruiz/elkbledom` → `domain: elkbledom`, `last_version: 1.6.5`, `stargazers_count: 198`, `open_issues: 7`. (Brief quoted 1.6.4; upstream has since released 1.6.5 — shape unchanged.) |
| 11 | `https://raw.githubusercontent.com/hacs/default/master/integration` | `curl` + `json` | HTTP 200, JSON array of **3 105** slugs, first three `007hacky007/car_maintenance`, `0jety0/emaux_spv150`, `0xAHA/airtouch4_advanced` — matches the brief. Usable fallback. |
| 12 | `https://codeload.github.com/dave-code-ruiz/elkbledom/tar.gz/refs/tags/1.6.5` | `curl -L` | HTTP 200, 69 860 bytes, members under `elkbledom-1.6.5/custom_components/elkbledom/*.py`. A bogus tag returns **HTTP 404**, confirming the tag→branch fallback path in the brief is needed and detectable. |
| 13 | `https://analytics.home-assistant.io/` | WebFetch | **663 473** active Home Assistant installations (brief quoted 662 933 at scan time; population has grown). |
| 14 | `https://github.com/custom-components/breaking_changes` | GitHub REST API | `archived: true`, `pushed_at: 2022-05-28`, 79 stars, 14 forks. The incumbent is confirmed dead. |
| 15 | Python `urllib.request` through this machine's proxy | live call | `HTTP 200`, 1 963 KB. The machine-wide `HTTP_PROXY=http://100.111.92.108:8888` (LESSONS 2026-08-03) does **not** break stdlib `urllib` — only `httplib2`. No workaround needed. |

## Cost model

**Zero cost.** No paid API key, account, or hosting is required:

* every data source above is unauthenticated public HTTP;
* GitHub Actions minutes and GitHub Pages are free for public repositories;
* the HA integration has `"requirements": []` — no third-party runtime dependencies.

No trials were started and no payment details were entered.

## LESSONS.md cross-check

Nothing in the brief contradicts `C:\Users\cbosc\claude-phone\ideas\LESSONS.md`. Two entries were
actively applied rather than merely noted:

* **2026-08-03 (machine-wide proxy)** — verified stdlib `urllib` is unaffected (row 15), so the
  crawler needs no proxy handling.
* **2026-07-27 (a static-analysis rule written from a spec is a HYPOTHESIS)** — this is why
  auto-derived rules pass a specificity gate and why every rule's hit-rate is measured against the
  real crawl before it ships. See `README.md` § "How rules are chosen" and `docs/index.json`
  `rules[].matchable`.
