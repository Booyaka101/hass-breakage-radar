# Changelog

All notable changes to Breakage Radar. Versions follow
[semver](https://semver.org/); the `custom_components/breakage_radar/manifest.json`
and `pyproject.toml` versions always agree (enforced by a test).

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
