"""Check a checkout on this machine against the published breakage index.

This is the self-check for integration and card *authors*: the daily crawler
only visits repositories listed in the HACS catalogue, so a fork, a private
integration or an unreleased branch can never be answered by the board. This
runs the exact same matchers over a directory you point it at.

    python tools/check_local.py .
    python tools/check_local.py ~/src/my-integration --ha-version 2027.5
    python tools/check_local.py . --rules data/rules.json    # offline

It accepts a repository checkout, or a ``custom_components`` directory itself.
A checkout with ``custom_components/`` is read as an integration: its Python,
plus any frontend module shipped alongside. A checkout without one is read as
a Lovelace card repository and its JavaScript is scanned wherever it lives.

Exit codes are the contract, because this runs in an author's own CI:

* ``0`` nothing to report
* ``1`` findings, and ``--fail-on`` says they are worth failing over
* ``2`` could not check, which is never the same thing as clean

``--fail-on`` defaults to ``any``, so the default is the release gate it has
always been. ``--format github`` turns the findings into workflow annotations
and a job summary; ``action.yml`` in the repository root is a thin wrapper
around exactly that. ``--require-complete`` additionally exits 2 for a partial
source scan, regardless of the findings policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import LOGGER, http_get_json, read_json, setup_logging  # noqa: E402
from tools.rules_engine import (  # noqa: E402
    JS_SUFFIXES,
    VENDOR_DIRECTORIES,
    Finding,
    Rule,
    ScanStats,
    clip,
    is_future,
    load_rules,
    looks_minified_js,
    parse_version,
    reports_before_removal,
    scan_sources,
)
from tools.schedule import days_until, describe_report, describe_when  # noqa: E402

INDEX_URL = "https://booyaka101.github.io/hass-breakage-radar/index.json"

#: Mirrors the integration's own caps so a local check and the sensor agree.
MAX_FILES = 400
MAX_BYTES = 1_000_000

#: How far ahead ``--fail-on imminent`` still counts as imminent. The board
#: leads with the same 90 days.
WINDOW_DAYS = 90

_SKIP_DIRECTORIES = frozenset({"__pycache__", ".git", ".github"}) | VENDOR_DIRECTORIES


def find_components_dir(target: Path) -> Path | None:
    """Accept either a repo checkout or the ``custom_components`` dir itself."""
    if target.name == "custom_components" and target.is_dir():
        return target
    candidate = target / "custom_components"
    if candidate.is_dir():
        return candidate
    return None


def _source_files(
    root: Path, suffixes: tuple[str, ...], skipped: dict[str, int] | None = None
) -> list[tuple[Path, str]]:
    """Prune excluded directories and surface walk errors, unlike Path.rglob.

    Dotted files remain source. Symlinked subdirectories are not followed, but
    a symlinked root still works for development checkouts.
    """
    def on_error(err: OSError) -> None:
        LOGGER.warning("could not walk %s: %s", root, err)
        if skipped is not None:
            skipped["unreadable_directories"] += 1

    files = []
    for directory, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in _SKIP_DIRECTORIES and not name.startswith(".")
        )
        for name in filenames:
            if name.endswith(suffixes):
                path = Path(directory) / name
                files.append((path, path.relative_to(root).as_posix()))
    return sorted(files)


def iter_python_files(
    domain_dir: Path, skipped: dict[str, int] | None = None
) -> list[tuple[Path, str]]:
    """``[(path, posix path relative to the component directory)]``."""
    return _source_files(domain_dir, (".py",), skipped)


def read_python(
    components: Path, skipped: dict[str, int], *, has_matchers: bool
) -> Iterator[tuple[str, bytes]]:
    """``(custom_components/... path, source)`` for every component on disk."""
    for domain_dir in _component_dirs(components):
        files = iter_python_files(domain_dir, skipped)
        if not has_matchers:
            skipped["no_matchers"] += len(files)
            continue
        for position, (path, relative) in enumerate(files):
            if position >= MAX_FILES:
                skipped["capped"] += len(files) - position
                break
            source = _read_bytes(path, skipped)
            if source is not None:
                yield f"custom_components/{domain_dir.name}/{relative}", source


def read_javascript(
    root: Path, skipped: dict[str, int], *, has_matchers: bool
) -> Iterator[tuple[str, str]]:
    """``(path, text)`` for every scannable frontend file below ``root``.

    A ``.d.ts`` declares the API rather than calling it, and a bundle is never
    guessed at: it is counted so a card repository whose only file is minified
    reads as *not analysed* instead of clean.
    """
    prefix = "custom_components/" if root.name == "custom_components" else ""
    for path, name in _source_files(root, JS_SUFFIXES, skipped):
        if name.endswith(".d.ts"):
            continue
        if not has_matchers:
            skipped["no_matchers"] += 1
            continue
        source = _read_bytes(path, skipped)
        if source is None:
            continue
        text = source.decode("utf-8", "replace")
        if looks_minified_js(name, text):
            skipped["minified"] += 1
            continue
        yield prefix + name, text


def _component_dirs(components: Path) -> list[Path]:
    return sorted(
        entry
        for entry in components.iterdir()
        if entry.is_dir()
        and entry.name not in _SKIP_DIRECTORIES
        and not entry.name.startswith(".")
    )


def _read_bytes(path: Path, skipped: dict[str, int]) -> bytes | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            skipped["too_big"] += 1
            return None
        return path.read_bytes()
    except OSError as err:
        LOGGER.warning("could not read %s: %s", path, err)
        skipped["unreadable"] += 1
        return None


def is_blocking(finding: Finding, policy: str, window_days: int, today: date) -> bool:
    """Whether this finding should fail the job under ``policy``.

    ``imminent`` forms no opinion about a release label that does not map to a
    date, the same way the integration refuses to guess one. It is still
    reported, just not fatal.
    """
    if policy == "never":
        return False
    if policy == "any":
        return True
    days = days_until(finding.breaks_in, today)
    return days is not None and days <= window_days


def _escape(value: str, *, property_value: bool = False) -> str:
    """Workflow-command escaping. Property values need the separators too."""
    out = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        out = out.replace(":", "%3A").replace(",", "%2C")
    return out


def _reports_from(rule: Rule | None, today: date) -> str:
    """When the rule warns before it breaks, the sentence saying so."""
    if rule is None or not reports_before_removal(rule.reports_in, rule.breaks_in):
        return ""
    return describe_report(rule.reports_in, days_until(rule.reports_in, today))


def _describe(finding: Finding, rule: Rule | None, today: date) -> tuple[str, str]:
    """``(title, one-line message)`` shared by both output formats."""
    when = describe_when(finding.breaks_in, days_until(finding.breaks_in, today))
    title = f"Breaks in Home Assistant {finding.breaks_in} ({when})"
    parts = [clip((rule.message if rule else "").strip(), 400) or finding.symbol]
    if reports := _reports_from(rule, today):
        parts.append(f"{reports}.")
    parts.append(f"[{finding.confidence} confidence, rule {finding.rule_id}]")
    if rule and rule.source:
        parts.append(rule.source)
    return title, " ".join(part for part in parts if part)


def render_text(findings: list[Finding], rules: dict[str, Rule], today: date) -> None:
    print()
    for finding in findings:
        rule = rules.get(finding.rule_id)
        print(f"{finding.file}:{finding.line}")
        print(
            f"    breaks in Home Assistant {finding.breaks_in} "
            f"({finding.confidence} confidence, rule {finding.rule_id})"
        )
        if reports := _reports_from(rule, today):
            print(f"    {reports[0].lower()}{reports[1:]}")
        if rule and rule.message:
            print(f"    {clip(rule.message, 200)}")
        if rule and rule.source:
            print(f"    {rule.source}")
        print()
    print(f"{len(findings)} finding(s).")


def render_json(
    findings: list[Finding],
    rules: dict[str, Rule],
    stats: ScanStats,
    skipped: dict[str, int],
    blocking: set[int],
    exit_code: int,
) -> None:
    """One machine-readable result; logs stay on stderr, not in the JSON."""
    payload = {
        "schema": 1,
        "exit_code": exit_code,
        "complete": is_complete(stats, skipped),
        "files_scanned": stats.files_scanned,
        "unparsed_files": len(stats.syntax_errors),
        "syntax_errors": stats.syntax_errors,
        "skipped": skipped,
        "blocking_count": len(blocking),
        "findings": [
            {
                **finding.to_dict(),
                "message": rules[finding.rule_id].message,
                "source": rules[finding.rule_id].source,
                "blocking": index in blocking,
            }
            for index, finding in enumerate(findings)
        ],
    }
    print(json.dumps(payload, indent=2))


def is_complete(stats: ScanStats, skipped: dict[str, int]) -> bool:
    """Source coverage, not a claim that every announced removal has a rule."""
    return bool(stats.files_scanned) and not stats.syntax_errors and not any(skipped.values())


def render_github(
    findings: list[Finding],
    rules: dict[str, Rule],
    today: date,
    blocking: set[int],
) -> None:
    """Workflow annotations, plus the whole list as a job summary.

    Both, because GitHub only displays 10 annotations per level per step and
    50 per job. A repository with thirty findings would otherwise show ten and
    read as though that was all of them.
    """
    for index, finding in enumerate(findings):
        title, message = _describe(finding, rules.get(finding.rule_id), today)
        level = "error" if index in blocking else "warning"
        print(
            f"::{level} file={_escape(finding.file, property_value=True)},"
            f"line={finding.line},"
            f"title={_escape(title, property_value=True)}::{_escape(message)}"
        )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## Home Assistant Breakage Radar",
        "",
        f"{len(findings)} finding(s) in this checkout.",
        "",
        "| Breaks in | When | File | Rule | Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        rule = rules.get(finding.rule_id)
        when = describe_when(finding.breaks_in, days_until(finding.breaks_in, today))
        # An earlier report release goes in the same cell rather than a column
        # of its own, which most rows would leave empty.
        breaks = finding.breaks_in
        if rule and reports_before_removal(rule.reports_in, rule.breaks_in):
            breaks += f" (warns from {rule.reports_in})"
        lines.append(
            f"| {breaks} | {when} | `{finding.file}:{finding.line}` "
            f"| {finding.rule_id} | {finding.confidence} |"
        )
    lines.extend(
        [
            "",
            "A finding is a static-analysis result, not a guarantee of breakage. "
            "Full detail, and how to report one you think is wrong: "
            "<https://github.com/Booyaka101/hass-breakage-radar/blob/main/guides/"
            "for-integration-authors.md>",
            "",
        ]
    )
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError as err:  # pragma: no cover - the runner always provides it
        LOGGER.warning("could not write the job summary: %s", err)


def _load_rules_payload(args: argparse.Namespace) -> tuple[dict, str] | None:
    if args.rules:
        payload = read_json(args.rules, default=None)
        if payload is None:
            LOGGER.error("could not load valid JSON from %s", args.rules)
            return None
        return (payload if isinstance(payload, dict) else {"rules": payload}), str(args.rules)
    try:
        index = http_get_json(args.index)
    except Exception as err:  # noqa: BLE001 - any transport problem is fatal here
        LOGGER.error(
            "could not fetch %s (%s). Use --rules data/rules.json to run offline.",
            args.index,
            err,
        )
        return None
    return index, args.index


def coverage_note(payload: dict, rules: list[Rule]) -> str:
    """What this check could not look for, so "clean" is read at its true size.

    ``index.json`` counts the gate's discards in ``coverage`` and ``rules.json``
    in ``counts``, pending ones only; an older file of either kind carries
    neither and gets no number.
    """
    listed = payload.get("rules", [])
    matchable = sum(1 for rule in rules if rule.matchable)
    note = f"{matchable} of {len(listed)} announced removals have a matcher"
    counts = payload.get("counts", {})
    discarded = payload.get("coverage", {}).get(
        "markers_discarded", counts.get("markers_discarded_pending")
    )
    if discarded:
        note += f"; {discarded} marker(s) too vague to match"
    return note


def _validate_rules_payload(payload: dict) -> None:
    """Reject malformed rule records before a partial set can look clean."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise ValueError("rules payload must contain a rules array")
    seen = set()
    for item in payload["rules"]:
        if not isinstance(item, dict):
            raise ValueError("each rule must be an object")
        for key in ("id", "breaks_in"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"rule {key} must be a nonempty string")
        if item["id"] in seen:
            raise ValueError(f"duplicate rule id: {item['id']}")
        seen.add(item["id"])
        for key in ("kind", "symbol", "message", "source", "origin", "confidence"):
            if key in item and not isinstance(item[key], str):
                raise ValueError(f"rule {item['id']}: {key} must be a string")
        matcher = item.get("match")
        if matcher is not None and (
            not isinstance(matcher, dict)
            or (bool(matcher) and not isinstance(matcher.get("type"), str))
        ):
            raise ValueError(f"rule {item['id']}: match must be an object with a type")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        type=Path,
        help="repository checkout, or a custom_components directory",
    )
    parser.add_argument(
        "--index", default=INDEX_URL, help="published index URL to take rules from"
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="use a local rules.json instead of fetching the index (offline)",
    )
    parser.add_argument(
        "--ha-version",
        default=None,
        help="only report removals still ahead of this Home Assistant release",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github", "json"),
        default="text",
        help="github emits workflow annotations; json emits a machine-readable report",
    )
    parser.add_argument(
        "--fail-on",
        choices=("never", "imminent", "any"),
        default="any",
        help="which findings exit 1; imminent means within --window-days",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=WINDOW_DAYS,
        help=f"how far ahead --fail-on imminent reaches (default {WINDOW_DAYS})",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 2 if any source could not be scanned, regardless of --fail-on",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.window_days < 0:
        parser.error("--window-days must be zero or greater")
    setup_logging(args.verbose)
    try:
        return _check(args)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as err:
        LOGGER.error("could not check: %s", err)
        return 2


def _check(args: argparse.Namespace) -> int:
    target = args.target.expanduser().resolve()
    if not target.is_dir():
        LOGGER.error("%s is not a directory", args.target)
        return 2

    rules_source = _load_rules_payload(args)
    if rules_source is None:
        return 2
    payload, source_label = rules_source
    _validate_rules_payload(payload)
    rules_payload = payload.get("rules", [])

    all_rules = load_rules(rules_payload)
    rules = [rule for rule in all_rules if rule.matchable]
    if args.ha_version:
        rules = [r for r in rules if is_future(r.breaks_in, args.ha_version)]
    if not rules:
        LOGGER.error("no matchable rules in %s -- nothing could be checked", source_label)
        return 2
    LOGGER.info("%d matchable rule(s) from %s", len(rules), source_label)
    LOGGER.info("%s", coverage_note(payload, all_rules))

    components = find_components_dir(target)
    skipped = {
        "capped": 0, "too_big": 0, "unreadable": 0, "minified": 0,
        "unreadable_directories": 0, "no_matchers": 0,
    }
    stats = ScanStats()
    matcher_types = {rule.match["type"] for rule in rules if rule.match}

    if components is None:
        # No custom_components: read it as a Lovelace card repository, whose
        # card can live anywhere in the tree.
        LOGGER.info("no custom_components/ in %s; scanning as a card repository", target)
        python_sources = ()
    elif not _component_dirs(components):
        LOGGER.error("%s contains no component directories", components)
        return 2
    else:
        python_sources = read_python(
            components, skipped, has_matchers=bool(matcher_types - {"js"}),
        )

    findings = scan_sources(
        python_sources,
        read_javascript(components or target, skipped, has_matchers="js" in matcher_types),
        rules,
        stats,
    )
    LOGGER.info(
        "scanned %d file(s); %d unparseable, %d source item(s) skipped",
        stats.files_scanned,
        len(stats.syntax_errors),
        sum(skipped.values()),
    )
    for problem in stats.syntax_errors:
        LOGGER.warning("could not parse %s", problem)
    if skipped["no_matchers"]:
        LOGGER.warning(
            "skipped %d source file(s): no applicable %s matchers in the selected rules",
            skipped["no_matchers"],
            "Python" if "js" in matcher_types else "JavaScript/TypeScript",
        )

    findings.sort(key=lambda f: (parse_version(f.breaks_in), f.file, f.line))
    messages = {rule.id: rule for rule in all_rules}
    today = date.today()
    blocking = {
        index
        for index, finding in enumerate(findings)
        if is_blocking(finding, args.fail_on, args.window_days, today)
    }
    incomplete = stats.files_scanned == 0 or (
        args.require_complete and not is_complete(stats, skipped)
    )
    exit_code = 2 if incomplete else (1 if blocking else 0)
    if args.format == "json":
        render_json(findings, messages, stats, skipped, blocking, exit_code)
        return exit_code

    if stats.files_scanned == 0:
        # Nothing was read, so nothing was proved. Reporting this as clean is
        # the one failure mode this tool exists to avoid.
        LOGGER.error(
            "no scannable source in %s (%d source item(s) skipped) -- nothing was checked",
            target,
            sum(skipped.values()),
        )
        return 2

    if args.require_complete and incomplete:
        LOGGER.error("source scan is incomplete; --require-complete requires exit 2")
        if args.format == "github":
            print("::error::Source scan is incomplete (--require-complete). See the scan logs.")

    if not findings and args.format == "text":
        # Plain ASCII: a Windows console defaults to cp1252 and raises on
        # anything fancier.
        if is_complete(stats, skipped):
            print("OK - no scheduled removals found in this checkout.")
        else:
            print("No scheduled removals found in the scanned source; scan incomplete.")
        return exit_code

    if args.format == "github":
        render_github(findings, messages, today, blocking)
    else:
        render_text(findings, messages, today)

    if not blocking and findings:
        LOGGER.info(
            "%d finding(s), none of them fatal under --fail-on %s",
            len(findings),
            args.fail_on,
        )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
