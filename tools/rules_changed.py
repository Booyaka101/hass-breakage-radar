#!/usr/bin/env python3
"""Has the rule set actually changed, or only its provenance stamps?

The crawl regenerates ``data/rules.json`` on every run, so ``generated_utc``,
``blog_merged_utc`` and the core tarball's sha move daily even when no rule
did. Committing that keeps the file in conflict with every open pull request
that touches it, and GitHub cannot build the merge ref for a conflicted pull
request, so it skips the ``pull_request`` workflows for it. The checks do not
fail, they never run, which reads exactly like a green branch.

Exits 0 when the rules really changed and the file is worth committing, 1 when
only the stamps moved.

Usage::

    python tools/rules_changed.py [--path data/rules.json] [--against HEAD]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.common import DATA_DIR, LOGGER, setup_logging  # noqa: E402

#: Keys that move on every run without saying anything about the rules. The
#: counts are derived from the rules themselves, so they are not listed here.
PROVENANCE_KEYS = frozenset(
    {
        "generated_utc",
        "blog_merged_utc",
        "core_tarball_sha256",
        "core_files_scanned",
        "core_files_unparsed",
        "python_version",
    }
)


def significant(payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a rules payload worth committing a change for."""
    return {k: v for k, v in payload.items() if k not in PROVENANCE_KEYS}


def rules_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """True when ``current`` differs from ``previous`` in more than provenance.

    A missing or unreadable previous payload counts as changed: committing a
    file nobody can compare against is the safe direction.
    """
    if previous is None:
        return True
    return significant(previous) != significant(current)


def committed_payload(path: Path, ref: str) -> dict[str, Any] | None:
    """``path`` as of ``ref``, or ``None`` if it is not there or not readable."""
    try:
        blob = subprocess.run(
            ["git", "show", f"{ref}:{path.as_posix()}"],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--path", type=Path, default=DATA_DIR / "rules.json")
    parser.add_argument("--against", default="HEAD", help="git ref to compare with")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if not args.path.exists():
        LOGGER.error("%s does not exist", args.path)
        return 1

    current = json.loads(args.path.read_text(encoding="utf-8"))
    relative = args.path
    try:
        relative = args.path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        pass
    previous = committed_payload(relative, args.against)

    if rules_changed(previous, current):
        LOGGER.info("%s: the rule set changed", args.path)
        return 0
    LOGGER.info("%s: only provenance moved; leaving it out of the commit", args.path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
