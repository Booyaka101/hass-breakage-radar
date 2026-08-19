#!/usr/bin/env python3
"""Merge rules + crawl findings into the public index and the static board.

Writes:

* ``docs/index.json`` -- schema 1, the machine-readable index the Home Assistant
  integration consumes.
* ``docs/index.html`` -- a dependency-free sortable board grouped by the Home
  Assistant release that does the removing.
* ``docs/feed.xml`` -- RSS of the announced removals, so following the project
  does not mean polling the index and diffing it yourself.
* ``docs/.nojekyll`` -- so GitHub Pages serves the JSON untouched.

Usage::

    python tools/build_index.py [--output-dir docs]
"""

from __future__ import annotations

import argparse
import collections
import html
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import (  # noqa: E402
    DATA_DIR,
    DOCS_DIR,
    LOGGER,
    STATE_DIR,
    read_json,
    setup_logging,
    utc_now_iso,
    write_json,
)
from tools.feed import build as build_feed  # noqa: E402
from tools.feed import update_first_seen
from tools.rules_engine import is_future, parse_version  # noqa: E402

SCHEMA_VERSION = 1
INDEX_URL = "https://booyaka101.github.io/hass-breakage-radar/index.json"

REQUIRED_TOP_LEVEL = ("schema", "generated_utc", "rules", "integrations")
REQUIRED_FINDING_KEYS = ("rule_id", "breaks_in", "file", "line", "confidence")


def validate_index(payload: dict[str, Any]) -> list[str]:
    """Return a list of schema-1 violations. Empty means valid."""
    problems: list[str] = []
    for key in REQUIRED_TOP_LEVEL:
        if key not in payload:
            problems.append(f"missing top-level key {key!r}")
    if payload.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema must be {SCHEMA_VERSION}, got {payload.get('schema')!r}")
    if not isinstance(payload.get("rules"), list):
        problems.append("rules must be a list")
    if not isinstance(payload.get("integrations"), list):
        problems.append("integrations must be a list")

    rule_ids = {r.get("id") for r in payload.get("rules", []) if isinstance(r, dict)}
    for rule in payload.get("rules", []):
        for key in ("id", "breaks_in", "message", "source"):
            if key not in rule:
                problems.append(f"rule {rule.get('id')!r} missing {key!r}")

    for integration in payload.get("integrations", []):
        for key in ("full_name", "domain", "findings"):
            if key not in integration:
                problems.append(
                    f"integration {integration.get('full_name')!r} missing {key!r}"
                )
        for finding in integration.get("findings", []):
            for key in REQUIRED_FINDING_KEYS:
                if key not in finding:
                    problems.append(
                        f"{integration.get('full_name')} finding missing {key!r}"
                    )
            if finding.get("rule_id") not in rule_ids:
                problems.append(
                    f"{integration.get('full_name')} references unknown rule "
                    f"{finding.get('rule_id')!r}"
                )
    return problems


def build_payload(
    rules_doc: dict[str, Any],
    findings_doc: dict[str, Any],
    catalog_doc: dict[str, Any],
) -> dict[str, Any]:
    current = rules_doc.get("core_version", "2026.9")
    future_rules = [
        rule
        for rule in rules_doc.get("rules", [])
        if is_future(rule["breaks_in"], current)
    ]

    catalog_by_name = {
        entry["full_name"]: entry
        for entry in catalog_doc.get("integrations", [])
        if isinstance(entry, dict)
    }

    repos: dict[str, Any] = findings_doc.get("repos", {})
    integrations: list[dict[str, Any]] = []
    clean_domains: list[str] = []
    unreachable: list[str] = []
    rule_hits: collections.Counter[str] = collections.Counter()
    rule_repos: collections.Counter[str] = collections.Counter()

    for full_name in sorted(repos):
        record = repos[full_name]
        catalog_entry = catalog_by_name.get(full_name, {})
        domain = record.get("domain") or catalog_entry.get("domain") or ""
        findings = [
            {key: finding[key] for key in REQUIRED_FINDING_KEYS}
            for finding in record.get("findings", [])
        ]

        if record.get("status") in ("unreachable", "error"):
            if domain:
                unreachable.append(domain)
            continue

        if not findings:
            if domain:
                clean_domains.append(domain)
            continue

        for finding in findings:
            rule_hits[finding["rule_id"]] += 1
        for rule_id in {f["rule_id"] for f in findings}:
            rule_repos[rule_id] += 1

        findings.sort(key=lambda f: (parse_version(f["breaks_in"]), f["file"], f["line"]))
        entry = {
            "full_name": full_name,
            "domain": domain,
            "domains": record.get("domains", [domain] if domain else []),
            "version": record.get("version", ""),
            "ref": record.get("ref", ""),
            "stargazers_count": catalog_entry.get(
                "stargazers_count", record.get("stargazers_count", 0)
            ),
            "repo_url": f"https://github.com/{full_name}",
            "scanned_utc": record.get("scanned_utc", ""),
            "earliest_breaks_in": findings[0]["breaks_in"],
            "findings": findings,
        }
        if record.get("upstream"):
            entry["upstream"] = record["upstream"]
        integrations.append(entry)

    integrations.sort(
        key=lambda i: (
            parse_version(i["earliest_breaks_in"]),
            -int(i.get("stargazers_count") or 0),
            i["full_name"].lower(),
        )
    )

    published_rules = []
    for rule in future_rules:
        entry = dict(rule)
        entry["hits"] = rule_hits.get(rule["id"], 0)
        entry["repos_hit"] = rule_repos.get(rule["id"], 0)
        published_rules.append(entry)
    published_rules.sort(key=lambda r: (parse_version(r["breaks_in"]), r["id"]))

    by_release: dict[str, list[str]] = collections.defaultdict(list)
    for integration in integrations:
        for release in sorted({f["breaks_in"] for f in integration["findings"]}):
            by_release[release].append(integration["domain"] or integration["full_name"])

    return {
        "schema": SCHEMA_VERSION,
        "generated_utc": utc_now_iso(),
        "index_url": INDEX_URL,
        "core_version": current,
        "core_tarball_sha256": rules_doc.get("core_tarball_sha256", ""),
        "catalog_source": catalog_doc.get("source", ""),
        "coverage": {
            "catalog_total": len(catalog_by_name),
            # Only repositories still in the catalogue. A renamed or delisted
            # repository keeps its findings record, so counting the file
            # itself can exceed catalog_total and read as nonsense.
            "repos_scanned": len(set(repos) & set(catalog_by_name)),
            "repos_delisted": len(set(repos) - set(catalog_by_name)),
            "repos_affected": len(integrations),
            "repos_clean": len(clean_domains),
            "repos_unreachable": len(unreachable),
            "findings_total": sum(len(i["findings"]) for i in integrations),
            "rules_published": len(published_rules),
            "rules_matchable": sum(1 for r in published_rules if r.get("matchable")),
        },
        "releases": {
            release: sorted(set(domains))
            for release, domains in sorted(
                by_release.items(), key=lambda kv: parse_version(kv[0])
            )
        },
        "rules": published_rules,
        "integrations": integrations,
        "clean_domains": sorted(set(clean_domains)),
        "unreachable_domains": sorted(set(unreachable)),
    }


# --------------------------------------------------------------------------- #
# static board
# --------------------------------------------------------------------------- #

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breakage Radar for Home Assistant</title>
<meta name="description" content="Which HACS custom integrations stop working, and in which Home Assistant release.">
<link rel="alternate" type="application/rss+xml" title="Breakage Radar: announced removals" href="feed.xml">
<style>
:root {{
  --bg: #0f1216; --panel: #171c22; --line: #262d36; --ink: #e6edf3;
  --muted: #8b98a5; --accent: #38bdf8; --warn: #f59e0b; --bad: #ef4444;
  --ok: #22c55e;
}}
@media (prefers-color-scheme: light) {{
  :root {{ --bg:#f6f8fa; --panel:#fff; --line:#d8dee4; --ink:#1f2328;
           --muted:#57606a; --accent:#0969da; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
header {{ padding:32px 20px 12px; max-width:1180px; margin:0 auto; }}
h1 {{ margin:0 0 6px; font-size:28px; letter-spacing:-.02em; }}
.sub {{ color:var(--muted); margin:0 0 18px; max-width:70ch; }}
main {{ max-width:1180px; margin:0 auto; padding:0 20px 60px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 26px; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:10px 14px; min-width:120px; }}
.stat b {{ display:block; font-size:22px; }}
.stat span {{ color:var(--muted); font-size:12px; text-transform:uppercase;
  letter-spacing:.06em; }}
.controls {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
input, select {{ background:var(--panel); color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:9px 12px; font:inherit; }}
input {{ flex:1; min-width:220px; }}
section.release {{ margin-bottom:34px; }}
section.release > h2 {{ font-size:19px; margin:0 0 4px;
  display:flex; align-items:center; gap:10px; }}
.pill {{ font-size:12px; font-weight:600; padding:2px 9px; border-radius:999px;
  background:var(--warn); color:#000; }}
.rulelist {{ margin:6px 0 14px; padding:0; list-style:none; color:var(--muted);
  font-size:13px; }}
.rulelist li {{ margin:3px 0; }}
.rulelist code {{ color:var(--ink); }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
  border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
th, td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line);
  vertical-align:top; font-size:14px; }}
th {{ cursor:pointer; user-select:none; font-size:12px; text-transform:uppercase;
  letter-spacing:.05em; color:var(--muted); white-space:nowrap; }}
th:hover {{ color:var(--ink); }}
tr:last-child td {{ border-bottom:none; }}
td.mono, code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:12.5px; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.hit {{ display:block; color:var(--muted); }}
.empty {{ color:var(--muted); padding:18px; background:var(--panel);
  border:1px solid var(--line); border-radius:10px; }}
footer {{ max-width:1180px; margin:0 auto; padding:0 20px 50px; color:var(--muted);
  font-size:13px; }}
.conf-high {{ color:var(--bad); }} .conf-medium {{ color:var(--warn); }}
.conf-low {{ color:var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>Breakage Radar for Home Assistant</h1>
  <p class="sub">Which HACS custom integrations use Home Assistant APIs that are
  already scheduled for removal &mdash; and exactly which release removes them.
  Generated from a real crawl of the HACS catalogue; nothing here is hand-entered
  or simulated.</p>
  <div class="stats">{stats}</div>
</header>
<main>
  <div class="controls">
    <input id="q" type="search" placeholder="Filter by repository, domain or rule&hellip;"
           aria-label="Filter">
    <select id="conf" aria-label="Minimum confidence">
      <option value="">All confidences</option>
      <option value="high">High only</option>
      <option value="medium">High + medium</option>
    </select>
  </div>
{sections}
</main>
<footer>
  <p>Index generated <strong>{generated}</strong> against Home Assistant core
  <strong>{core}</strong>. Machine-readable:
  <a href="index.json">index.json</a> (schema 1).</p>
  <p>Install the companion Home Assistant integration to see only <em>your</em>
  affected integrations:
  <a href="https://github.com/Booyaka101/hass-breakage-radar">Booyaka101/hass-breakage-radar</a>.</p>
  <p>A finding is a static-analysis result, not a guarantee of breakage. Rules
  marked <span class="conf-medium">medium</span> can match a same-named symbol
  from a different module when the receiver is only known at runtime.</p>
</footer>
<script>
const q = document.getElementById('q'), conf = document.getElementById('conf');
const RANK = {{high: 3, medium: 2, low: 1, info: 0}};
function apply() {{
  const needle = q.value.trim().toLowerCase();
  const floor = conf.value ? RANK[conf.value] : 0;
  document.querySelectorAll('section.release').forEach(section => {{
    let shown = 0;
    section.querySelectorAll('tbody tr').forEach(row => {{
      const okText = !needle || row.dataset.search.includes(needle);
      const okConf = RANK[row.dataset.conf] >= floor;
      const visible = okText && okConf;
      row.hidden = !visible;
      if (visible) shown++;
    }});
    section.hidden = shown === 0;
    const counter = section.querySelector('.pill');
    if (counter) counter.textContent = shown + ' integration' + (shown === 1 ? '' : 's');
  }});
}}
q.addEventListener('input', apply);
conf.addEventListener('change', apply);
document.querySelectorAll('table').forEach(table => {{
  table.querySelectorAll('th').forEach((th, column) => {{
    th.addEventListener('click', () => {{
      const body = table.tBodies[0];
      const rows = Array.from(body.rows);
      const desc = th.dataset.dir !== 'desc';
      table.querySelectorAll('th').forEach(o => o.dataset.dir = '');
      th.dataset.dir = desc ? 'desc' : 'asc';
      rows.sort((a, b) => {{
        const x = a.cells[column].dataset.sort ?? a.cells[column].textContent;
        const y = b.cells[column].dataset.sort ?? b.cells[column].textContent;
        const nx = parseFloat(x), ny = parseFloat(y);
        const cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y));
        return desc ? -cmp : cmp;
      }});
      rows.forEach(r => body.appendChild(r));
    }});
  }});
}});
apply();
</script>
</body>
</html>
"""


def _stat(value: Any, label: str) -> str:
    return f'<div class="stat"><b>{html.escape(str(value))}</b><span>{html.escape(label)}</span></div>'


def render_html(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    stats = "".join(
        [
            _stat(coverage["repos_affected"], "affected integrations"),
            _stat(coverage["findings_total"], "findings"),
            _stat(coverage["repos_scanned"], "repos scanned"),
            _stat(coverage["catalog_total"], "in HACS catalogue"),
            _stat(coverage["rules_matchable"], "active rules"),
            _stat(payload["core_version"], "core version"),
        ]
    )

    rules_by_id = {rule["id"]: rule for rule in payload["rules"]}
    by_release: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for integration in payload["integrations"]:
        for release in sorted({f["breaks_in"] for f in integration["findings"]}):
            by_release[release].append(integration)

    sections: list[str] = []
    for release in sorted(by_release, key=parse_version):
        entries = by_release[release]
        release_rules = sorted(
            {
                f["rule_id"]
                for integration in entries
                for f in integration["findings"]
                if f["breaks_in"] == release
            }
        )
        rule_items = "".join(
            f"<li><code>{html.escape(rid)}</code> &mdash; "
            f"{html.escape((rules_by_id.get(rid, {}).get('message') or '')[:200])} "
            f"<a href=\"{html.escape(rules_by_id.get(rid, {}).get('source', ''))}\">source</a></li>"
            for rid in release_rules
        )

        rows: list[str] = []
        for integration in entries:
            hits = [f for f in integration["findings"] if f["breaks_in"] == release]
            best = max(
                (f["confidence"] for f in hits),
                key=lambda c: {"high": 3, "medium": 2, "low": 1, "info": 0}.get(c, 0),
                default="medium",
            )
            search = " ".join(
                [integration["full_name"], integration["domain"]]
                + [f["rule_id"] for f in hits]
            ).lower()
            detail = "".join(
                f'<span class="hit"><code>{html.escape(f["rule_id"])}</code> '
                f'&middot; {html.escape(f["file"])}:{f["line"]}</span>'
                for f in hits[:6]
            )
            if len(hits) > 6:
                detail += f'<span class="hit">&hellip; and {len(hits) - 6} more</span>'
            rows.append(
                f'<tr data-search="{html.escape(search)}" data-conf="{html.escape(best)}">'
                f'<td><a href="{html.escape(integration["repo_url"])}">'
                f'{html.escape(integration["full_name"])}</a></td>'
                f'<td class="mono">{html.escape(integration["domain"])}</td>'
                f'<td class="mono">{html.escape(integration["version"])}</td>'
                f'<td data-sort="{int(integration.get("stargazers_count") or 0)}">'
                f'{integration.get("stargazers_count") or 0}</td>'
                f'<td data-sort="{len(hits)}" class="conf-{html.escape(best)}">{len(hits)}</td>'
                f"<td>{detail}</td></tr>"
            )

        sections.append(
            f'<section class="release" id="release-{html.escape(release)}">'
            f"<h2>Home Assistant {html.escape(release)}"
            f'<span class="pill">{len(entries)} integrations</span></h2>'
            f'<ul class="rulelist">{rule_items}</ul>'
            "<table><thead><tr>"
            "<th>Repository</th><th>Domain</th><th>Version</th><th>Stars</th>"
            "<th>Findings</th><th>Where</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></section>"
        )

    if not sections:
        sections.append(
            '<p class="empty">No affected integrations in the crawled slice yet. '
            "The daily crawl widens coverage automatically.</p>"
        )

    return PAGE_TEMPLATE.format(
        stats=stats,
        sections="\n".join(sections),
        generated=html.escape(payload["generated_utc"]),
        core=html.escape(payload["core_version"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rules", type=Path, default=DATA_DIR / "rules.json")
    parser.add_argument("--findings", type=Path, default=DATA_DIR / "findings.json")
    parser.add_argument("--catalog", type=Path, default=DATA_DIR / "catalog.json")
    parser.add_argument("--output-dir", type=Path, default=DOCS_DIR)
    parser.add_argument("--first-seen", type=Path, default=STATE_DIR / "feed.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    rules_doc = read_json(args.rules, default=None)
    findings_doc = read_json(args.findings, default=None)
    catalog_doc = read_json(args.catalog, default=None)

    missing = [
        str(path)
        for path, doc in (
            (args.rules, rules_doc),
            (args.findings, findings_doc),
            (args.catalog, catalog_doc),
        )
        if doc is None
    ]
    if missing:
        LOGGER.error(
            "missing input(s): %s -- run extract_rules.py, catalog.py and scan.py first",
            ", ".join(missing),
        )
        return 2

    payload = build_payload(rules_doc, findings_doc, catalog_doc)

    problems = validate_index(payload)
    if problems:
        for problem in problems[:20]:
            LOGGER.error("schema violation: %s", problem)
        LOGGER.error("%d schema violation(s); refusing to publish", len(problems))
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "index.json", payload, indent=1)
    (args.output_dir / "index.html").write_text(
        render_html(payload), encoding="utf-8", newline="\n"
    )
    (args.output_dir / ".nojekyll").write_text("", encoding="utf-8")

    seen = update_first_seen(
        payload["rules"],
        read_json(args.first_seen, default={}) or {},
        now=payload["generated_utc"],
    )
    write_json(args.first_seen, seen, indent=1)
    (args.output_dir / "feed.xml").write_text(
        build_feed(payload, seen), encoding="utf-8", newline="\n"
    )

    coverage = payload["coverage"]
    LOGGER.info(
        "wrote %s and index.html: %d affected of %d scanned (%d findings, %d rules)",
        args.output_dir / "index.json",
        coverage["repos_affected"],
        coverage["repos_scanned"],
        coverage["findings_total"],
        coverage["rules_published"],
    )
    for release, domains in payload["releases"].items():
        LOGGER.info("  %s: %d integration(s)", release, len(domains))
    LOGGER.info("rule hit-rates (repos hit / repos scanned):")
    for rule in sorted(payload["rules"], key=lambda r: -r["repos_hit"]):
        if rule["repos_hit"]:
            LOGGER.info(
                "  %-58s %4d repo(s)  %5.1f%%",
                rule["id"][:58],
                rule["repos_hit"],
                100.0 * rule["repos_hit"] / max(1, coverage["repos_scanned"]),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
