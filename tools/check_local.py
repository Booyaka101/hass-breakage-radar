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
around exactly that.
"""

from __future__ import annotations

import argparse
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
    is_future,
    load_rules,
    looks_minified_js,
    scan_sources,
)
from tools.schedule import days_until, describe_when  # noqa: E402

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


def _walkable(relative: Path) -> bool:
    """Neither vendored nor hidden. A finding in ``node_modules`` is not the
    repository's to fix, and ``.git`` is not source."""
    return not any(
        part in _SKIP_DIRECTORIES or part.startswith(".") for part in relative.parts[:-1]
    )


def iter_python_files(domain_dir: Path) -> list[tuple[Path, str]]:
    """``[(path, posix path relative to the component directory)]``."""
    return [
        (path, path.relative_to(domain_dir).as_posix())
        for path in sorted(domain_dir.rglob("*.py"))
        if _walkable(path.relative_to(domain_dir))
    ]


def read_python(
    components: Path, skipped: dict[str, int]
) -> Iterator[tuple[str, bytes]]:
    """``(custom_components/... path, source)`` for every component on disk."""
    for domain_dir in _component_dirs(components):
        files = iter_python_files(domain_dir)
        for position, (path, relative) in enumerate(files):
            if position >= MAX_FILES:
                skipped["capped"] += len(files) - position
                break
            source = _read_bytes(path, skipped)
            if source is not None:
                yield f"custom_components/{domain_dir.name}/{relative}", source


def read_javascript(root: Path, skipped: dict[str, int]) -> Iterator[tuple[str, str]]:
    """``(path, text)`` for every scannable frontend file below ``root``.

    A ``.d.ts`` declares the API rather than calling it, and a bundle is never
    guessed at: it is counted so a card repository whose only file is minified
    reads as *not analysed* instead of clean.
    """
    prefix = "custom_components/" if root.name == "custom_components" else ""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not _walkable(relative):
            continue
        name = relative.as_posix()
        if not name.endswith(JS_SUFFIXES) or name.endswith(".d.ts"):
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


def _describe(finding: Finding, rule: Rule | None, today: date) -> tuple[str, str]:
    """``(title, one-line message)`` shared by both output formats."""
    when = describe_when(finding.breaks_in, days_until(finding.breaks_in, today))
    title = f"Breaks in Home Assistant {finding.breaks_in} ({when})"
    parts = [(rule.message if rule else "").strip()[:400] or finding.symbol]
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
        if rule and rule.message:
            print(f"    {rule.message[:200]}")
        if rule and rule.source:
            print(f"    {rule.source}")
        print()
    print(f"{len(findings)} finding(s).")


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
        when = describe_when(finding.breaks_in, days_until(finding.breaks_in, today))
        lines.append(
            f"| {finding.breaks_in} | {when} | `{finding.file}:{finding.line}` "
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


def _load_rules_payload(args: argparse.Namespace) -> tuple[list[dict], str] | None:
    if args.rules:
        payload = read_json(args.rules, default=None)
        if payload is None:
            LOGGER.error("%s not found", args.rules)
            return None
        return payload.get("rules", payload), str(args.rules)
    try:
        index = http_get_json(args.index)
    except Exception as err:  # noqa: BLE001 - any transport problem is fatal here
        LOGGER.error(
            "could not fetch %s (%s). Use --rules data/rules.json to run offline.",
            args.index,
            err,
        )
        return None
    return index.get("rules", []), args.index


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
        choices=("text", "github"),
        default="text",
        help="github emits workflow annotations and a job summary",
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
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    target = args.target.expanduser().resolve()
    if not target.is_dir():
        LOGGER.error("%s is not a directory", args.target)
        return 2

    rules_source = _load_rules_payload(args)
    if rules_source is None:
        return 2
    rules_payload, source_label = rules_source

    rules = [rule for rule in load_rules(rules_payload) if rule.matchable]
    if args.ha_version:
        rules = [r for r in rules if is_future(r.breaks_in, args.ha_version)]
    if not rules:
        LOGGER.error("no matchable rules in %s -- nothing could be checked", source_label)
        return 2
    LOGGER.info("%d matchable rule(s) from %s", len(rules), source_label)

    components = find_components_dir(target)
    skipped = {"capped": 0, "too_big": 0, "unreadable": 0, "minified": 0}
    stats = ScanStats()

    if components is None:
        # No custom_components: read it as a Lovelace card repository, whose
        # card can live anywhere in the tree.
        LOGGER.info("no custom_components/ in %s; scanning as a card repository", target)
        findings = scan_sources((), read_javascript(target, skipped), rules, stats)
    elif not _component_dirs(components):
        LOGGER.error("%s contains no component directories", components)
        return 2
    else:
        findings = scan_sources(
            read_python(components, skipped),
            read_javascript(components, skipped),
            rules,
            stats,
        )

    LOGGER.info(
        "scanned %d file(s); %d unparseable, %d skipped",
        stats.files_scanned,
        len(stats.syntax_errors),
        sum(skipped.values()),
    )
    for problem in stats.syntax_errors:
        LOGGER.warning("could not parse %s", problem)

    if stats.files_scanned == 0:
        # Nothing was read, so nothing was proved. Reporting this as clean is
        # the one failure mode this tool exists to avoid.
        LOGGER.error(
            "no scannable source in %s (%d file(s) skipped) -- nothing was checked",
            target,
            sum(skipped.values()),
        )
        return 2

    if not findings:
        # Plain ASCII: a Windows console defaults to cp1252 and raises on
        # anything fancier.
        print("OK - no scheduled removals found in this checkout.")
        return 0

    findings.sort(key=lambda f: (f.breaks_in, f.file, f.line))
    messages = {rule.id: rule for rule in load_rules(rules_payload)}
    today = date.today()
    blocking = {
        index
        for index, finding in enumerate(findings)
        if is_blocking(finding, args.fail_on, args.window_days, today)
    }

    if args.format == "github":
        render_github(findings, messages, today, blocking)
    else:
        render_text(findings, messages, today)

    if not blocking:
        LOGGER.info(
            "%d finding(s), none of them fatal under --fail-on %s",
            len(findings),
            args.fail_on,
        )
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
