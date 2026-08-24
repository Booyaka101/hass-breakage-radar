#!/usr/bin/env python3
"""Flag open pull requests whose checks GitHub is silently skipping.

GitHub cannot build ``refs/pull/N/merge`` for a conflicted pull request, so it
skips that pull request's ``pull_request`` workflows entirely. The checks do not
fail, they never run, and the pull request page shows no checks at all rather
than a red one. It reads like a branch nobody has pushed to yet.

``tools/rules_changed.py`` removed the commonest cause, the daily crawl
rewriting ``data/rules.json``. It cannot remove the rest: the crawl also
commits ``docs/`` on every run, and those files genuinely change daily, so any
branch touching the board conflicts within hours.

No workflow inside the repository can detect this, because the workflow that
would report it is the one being skipped. So this runs on a schedule against
the default branch instead, labels what is conflicted, and says once why it
matters.

Usage::

    python tools/pr_health.py --repo owner/name
    python tools/pr_health.py --repo owner/name --dry-run
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

from tools.common import LOGGER, _sleep, setup_logging  # noqa: E402

LABEL = "conflicted"
LABEL_COLOUR = "d93f0b"
LABEL_DESCRIPTION = "Conflicts with the base branch, so GitHub is skipping its checks"

#: Left in the thread once, when the label goes on. The marker is how a second
#: run knows not to say it again.
MARKER = "<!-- pr-health:conflicted -->"

COMMENT = f"""{MARKER}
This branch conflicts with `main`, and that is worth more than the usual nudge:
GitHub cannot build the merge ref for a conflicted pull request, so it **skips
this pull request's checks entirely**. They are not failing, they never ran, and
the checks section shows nothing rather than something red.

Almost always the daily crawl, which commits `data/rules.json`, `docs/index.json`,
`docs/index.html`, `docs/feed.xml` and `state/`. If this branch touches the board,
rebase onto `main` and regenerate rather than resolving by hand:

```bash
git fetch origin main
git rebase origin/main
git checkout origin/main -- docs/index.json docs/index.html docs/feed.xml
python tools/build_index.py
git add -A && git rebase --continue
```

The label comes off by itself on the next run once this is mergeable.
"""


def _gh(args: list[str], *, check: bool = True) -> str:
    """Run ``gh`` and return stdout. Split out so tests never shell out."""
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if check:
            raise RuntimeError(f"gh {' '.join(args)} failed: {message}")
        LOGGER.warning("gh %s failed: %s", " ".join(args), message)
        return ""
    return result.stdout


def plan(pulls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What to do about each pull request, as ``{number, action, comment}``.

    Only the two states GitHub is definite about are acted on.
    ``mergeable: "UNKNOWN"`` means it has not finished computing, which is not
    the same as either: labelling on it would flag a clean branch, and clearing
    on it would drop a real warning.
    """
    actions: list[dict[str, Any]] = []
    for pull in pulls:
        labelled = LABEL in {label.get("name") for label in pull.get("labels") or []}
        state = pull.get("mergeable")
        if state == "CONFLICTING" and not labelled:
            actions.append(
                {
                    "number": pull["number"],
                    "action": "add",
                    # A pull request already carrying the explanation was
                    # labelled before, cleared, and has conflicted again. The
                    # label is the signal; the essay does not need repeating.
                    "comment": not pull.get("explained", False),
                }
            )
        elif state == "MERGEABLE" and labelled:
            actions.append({"number": pull["number"], "action": "remove", "comment": False})
    return actions


def resolve_mergeable(repo: str, pull: dict[str, Any]) -> None:
    """Turn an ``UNKNOWN`` into a real answer where possible.

    GitHub computes mergeability lazily and a list query often gets ``UNKNOWN``
    back for a pull request it has not looked at recently. Asking for the single
    pull request starts that computation, so the next answer is real. Without
    this the whole check quietly does nothing, which is the failure mode it
    exists to catch.
    """
    for _ in range(3):
        if pull.get("mergeable") != "UNKNOWN":
            return
        _sleep(2.0)
        raw = _gh(
            ["pr", "view", str(pull["number"]), "--repo", repo, "--json", "mergeable"],
            check=False,
        )
        if not raw:
            return
        pull["mergeable"] = json.loads(raw).get("mergeable", "UNKNOWN")


def fetch_pulls(repo: str) -> list[dict[str, Any]]:
    """Open pull requests, each annotated with whether it was already told."""
    raw = _gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,mergeable,labels",
        ]
    )
    pulls = json.loads(raw or "[]")
    for pull in pulls:
        resolve_mergeable(repo, pull)
    for pull in pulls:
        if LABEL in {label.get("name") for label in pull.get("labels") or []}:
            continue
        if pull.get("mergeable") != "CONFLICTING":
            continue
        body = _gh(
            [
                "api",
                f"repos/{repo}/issues/{pull['number']}/comments",
                "--jq",
                ".[].body",
            ],
            check=False,
        )
        pull["explained"] = MARKER in body
    return pulls


def ensure_label(repo: str) -> None:
    _gh(
        [
            "label",
            "create",
            LABEL,
            "--repo",
            repo,
            "--color",
            LABEL_COLOUR,
            "--description",
            LABEL_DESCRIPTION,
            "--force",
        ],
        check=False,
    )


def apply(repo: str, actions: list[dict[str, Any]]) -> int:
    """Carry out the plan. Returns how many pull requests could not be updated.

    One pull request failing does not skip the rest: this exists so a missing
    signal gets noticed, and dropping the other pull requests on the floor
    because of the first would be the same bug in a different place. The count
    comes back so the run still fails loudly.
    """
    failed = 0
    for action in actions:
        number = str(action["number"])
        try:
            if action["action"] == "add":
                _gh(["pr", "edit", number, "--repo", repo, "--add-label", LABEL])
                if action["comment"]:
                    _gh(["pr", "comment", number, "--repo", repo, "--body", COMMENT])
                LOGGER.warning(
                    "PR #%s conflicts with its base, so its checks are being skipped",
                    number,
                )
            else:
                _gh(["pr", "edit", number, "--repo", repo, "--remove-label", LABEL])
                LOGGER.info("PR #%s is mergeable again; label removed", number)
        except RuntimeError as err:
            failed += 1
            LOGGER.error("could not update PR #%s: %s", number, err)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--dry-run", action="store_true", help="report without labelling anything"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    pulls = fetch_pulls(args.repo)
    actions = plan(pulls)
    LOGGER.info("%d open pull request(s), %d action(s)", len(pulls), len(actions))

    unknown = [p["number"] for p in pulls if p.get("mergeable") == "UNKNOWN"]
    if unknown:
        LOGGER.info("mergeability not computed yet for %s; left alone", unknown)

    if args.dry_run:
        for action in actions:
            print(f"would {action['action']} {LABEL} on #{action['number']}")
        return 0

    if not actions:
        return 0
    ensure_label(args.repo)
    failed = apply(args.repo, actions)
    if failed:
        LOGGER.error("%d of %d pull request(s) could not be updated", failed, len(actions))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
