"""Check a checkout on this machine against the published breakage index.

This is the self-check for integration *authors*: the daily crawler only
visits repositories listed in the HACS catalogue, so a fork, a private
integration or an unreleased branch can never be answered by the board. This
runs the exact same matchers over a directory you point it at.

    python tools/check_local.py .
    python tools/check_local.py ~/src/my-integration --ha-version 2027.5
    python tools/check_local.py . --rules data/rules.json    # offline

It accepts either a repository checkout containing ``custom_components/`` or a
``custom_components`` directory itself, and exits 1 when it finds something --
so it works as a release gate in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import LOGGER, http_get_json, read_json, setup_logging  # noqa: E402
from tools.rules_engine import (  # noqa: E402
    ScanStats,
    is_future,
    load_rules,
    match_source,
)

INDEX_URL = "https://booyaka101.github.io/hass-breakage-radar/index.json"

#: Mirrors the integration's own caps so a local check and the sensor agree.
MAX_FILES = 400
MAX_BYTES = 1_000_000

_SKIP_DIRECTORIES = frozenset(
    {"__pycache__", ".git", ".github", "site-packages", "node_modules", "vendor"}
)


def find_components_dir(target: Path) -> Path | None:
    """Accept either a repo checkout or the ``custom_components`` dir itself."""
    if target.name == "custom_components" and target.is_dir():
        return target
    candidate = target / "custom_components"
    if candidate.is_dir():
        return candidate
    return None


def iter_python_files(domain_dir: Path) -> list[tuple[Path, str]]:
    """``[(path, posix path relative to the component directory)]``."""
    files: list[tuple[Path, str]] = []
    for path in sorted(domain_dir.rglob("*.py")):
        relative = path.relative_to(domain_dir)
        if any(part in _SKIP_DIRECTORIES for part in relative.parts):
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        files.append((path, relative.as_posix()))
    return files


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    components = find_components_dir(args.target.expanduser().resolve())
    if components is None:
        LOGGER.error(
            "no custom_components/ directory in %s -- point this at your "
            "repository checkout or at the custom_components directory itself",
            args.target,
        )
        return 2

    if args.rules:
        payload = read_json(args.rules, default=None)
        if payload is None:
            LOGGER.error("%s not found", args.rules)
            return 2
        rules_payload = payload.get("rules", payload)
        source_label = str(args.rules)
    else:
        try:
            index = http_get_json(args.index)
        except Exception as err:  # noqa: BLE001 - any transport problem is fatal here
            LOGGER.error(
                "could not fetch %s (%s). Use --rules data/rules.json to run offline.",
                args.index,
                err,
            )
            return 2
        rules_payload = index.get("rules", [])
        source_label = args.index

    rules = [rule for rule in load_rules(rules_payload) if rule.matchable]
    if args.ha_version:
        rules = [r for r in rules if is_future(r.breaks_in, args.ha_version)]
    if not rules:
        LOGGER.error("no matchable rules in %s -- nothing could be checked", source_label)
        return 2
    LOGGER.info("%d matchable rule(s) from %s", len(rules), source_label)

    messages = {rule.id: rule for rule in load_rules(rules_payload)}
    stats = ScanStats()
    findings = []
    skipped = 0

    domains = sorted(
        d for d in components.iterdir() if d.is_dir() and d.name not in _SKIP_DIRECTORIES
    )
    if not domains:
        LOGGER.error("%s contains no component directories", components)
        return 2

    for domain_dir in domains:
        files = iter_python_files(domain_dir)
        for position, (path, relative) in enumerate(files):
            if position >= MAX_FILES:
                skipped += len(files) - position
                break
            try:
                if path.stat().st_size > MAX_BYTES:
                    skipped += 1
                    continue
                source = path.read_bytes()
            except OSError as err:
                LOGGER.warning("could not read %s: %s", path, err)
                skipped += 1
                continue
            findings.extend(
                match_source(
                    f"custom_components/{domain_dir.name}/{relative}",
                    source,
                    rules,
                    stats,
                )
            )

    LOGGER.info(
        "scanned %d file(s) across %d component(s); %d unparseable, %d skipped",
        stats.files_scanned,
        len(domains),
        len(stats.syntax_errors),
        skipped,
    )
    for problem in stats.syntax_errors:
        LOGGER.warning("could not parse %s", problem)

    if not findings:
        # Plain ASCII: a Windows console defaults to cp1252 and raises on
        # anything fancier.
        print("OK - no scheduled removals found in this checkout.")
        return 0

    findings.sort(key=lambda f: (f.breaks_in, f.file, f.line))
    print()
    for finding in findings:
        rule = messages.get(finding.rule_id)
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
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
