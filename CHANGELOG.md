# Changelog

All notable changes to Breakage Radar. Versions follow
[semver](https://semver.org/); the `custom_components/breakage_radar/manifest.json`
and `pyproject.toml` versions always agree (enforced by a test).

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
