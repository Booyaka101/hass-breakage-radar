"""The conflicted-PR watcher.

The thing it guards against is an *absence*: GitHub skips a conflicted pull
request's checks rather than failing them, so the page shows no checks at all.
The decision logic is what matters here, and it is pure.
"""

from __future__ import annotations

import json

import pytest

from tools import pr_health
from tools.pr_health import LABEL, MARKER, plan


def _pr(number: int, mergeable: str, *, labelled: bool = False, explained: bool = False):
    return {
        "number": number,
        "title": f"pr {number}",
        "mergeable": mergeable,
        "labels": [{"name": LABEL}] if labelled else [],
        "explained": explained,
    }


def test_a_conflicted_pull_request_is_labelled_and_told_why():
    assert plan([_pr(1, "CONFLICTING")]) == [
        {"number": 1, "action": "add", "comment": True}
    ]


def test_a_mergeable_pull_request_is_left_alone():
    assert plan([_pr(1, "MERGEABLE")]) == []


def test_the_label_comes_off_once_it_is_mergeable_again():
    assert plan([_pr(1, "MERGEABLE", labelled=True)]) == [
        {"number": 1, "action": "remove", "comment": False}
    ]


def test_a_still_conflicted_pull_request_is_not_nagged_twice():
    assert plan([_pr(1, "CONFLICTING", labelled=True)]) == []


def test_the_explanation_is_not_repeated_when_it_conflicts_again():
    """Labelled, cleared, conflicted again: the label is the signal."""
    assert plan([_pr(1, "CONFLICTING", explained=True)]) == [
        {"number": 1, "action": "add", "comment": False}
    ]


def test_uncomputed_mergeability_is_not_a_guess_in_either_direction():
    """UNKNOWN is neither conflicted nor mergeable. Acting on it would either
    label a clean branch or clear a real warning."""
    assert plan([_pr(1, "UNKNOWN")]) == []
    assert plan([_pr(1, "UNKNOWN", labelled=True)]) == []


def test_a_missing_mergeable_field_is_treated_as_unknown():
    assert plan([{"number": 1, "labels": [{"name": LABEL}]}]) == []


def test_several_pull_requests_are_planned_independently():
    actions = plan(
        [
            _pr(1, "CONFLICTING"),
            _pr(2, "MERGEABLE", labelled=True),
            _pr(3, "MERGEABLE"),
            _pr(4, "UNKNOWN"),
        ]
    )
    assert [(a["number"], a["action"]) for a in actions] == [(1, "add"), (2, "remove")]


# --------------------------------------------------------------------------- #
# the gh layer, with gh stubbed out
# --------------------------------------------------------------------------- #


@pytest.fixture
def calls(monkeypatch):
    """Record every gh invocation and answer from a scripted table."""
    seen: list[list[str]] = []
    answers: dict[str, str] = {}

    def fake(args, *, check=True):
        seen.append(args)
        for key, value in answers.items():
            if key in " ".join(args):
                return value
        return ""

    monkeypatch.setattr(pr_health, "_gh", fake)
    monkeypatch.setattr(pr_health, "_sleep", lambda _seconds: None)
    return seen, answers


# --------------------------------------------------------------------------- #
# lazy mergeability, which would otherwise make the whole check a no-op
# --------------------------------------------------------------------------- #


def test_an_unknown_is_re_asked_until_github_answers(calls):
    """`gh pr list` routinely reports UNKNOWN for a PR GitHub has not looked at.
    Taking that at face value means this tool never fires at all."""
    seen, answers = calls
    answers["pr view"] = json.dumps({"mergeable": "CONFLICTING"})
    pull = _pr(1, "UNKNOWN")
    pr_health.resolve_mergeable("acme/thing", pull)

    assert pull["mergeable"] == "CONFLICTING"
    assert len([a for a in seen if "pr view" in " ".join(a)]) == 1


def test_a_known_state_is_not_re_asked(calls):
    seen, _ = calls
    pull = _pr(1, "MERGEABLE")
    pr_health.resolve_mergeable("acme/thing", pull)
    assert seen == []


def test_it_gives_up_rather_than_asking_forever(calls):
    seen, answers = calls
    answers["pr view"] = json.dumps({"mergeable": "UNKNOWN"})
    pull = _pr(1, "UNKNOWN")
    pr_health.resolve_mergeable("acme/thing", pull)

    assert pull["mergeable"] == "UNKNOWN"
    assert len([a for a in seen if "pr view" in " ".join(a)]) == 3


def test_a_failed_lookup_leaves_it_unknown_rather_than_guessing(calls):
    seen, answers = calls  # no answer scripted, so gh returns ""
    pull = _pr(1, "UNKNOWN")
    pr_health.resolve_mergeable("acme/thing", pull)
    assert pull["mergeable"] == "UNKNOWN"
    assert plan([pull]) == []


def test_a_listed_unknown_becomes_a_real_action(calls):
    """End to end through fetch_pulls: list says UNKNOWN, view says CONFLICTING,
    and the pull request gets labelled."""
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "UNKNOWN")])
    answers["pr view"] = json.dumps({"mergeable": "CONFLICTING"})
    pulls = pr_health.fetch_pulls("acme/thing")
    assert plan(pulls) == [{"number": 1, "action": "add", "comment": True}]


def test_fetch_only_asks_for_comments_on_an_unlabelled_conflict(calls):
    seen, answers = calls
    answers["pr list"] = json.dumps(
        [
            _pr(1, "CONFLICTING"),
            _pr(2, "CONFLICTING", labelled=True),
            _pr(3, "MERGEABLE"),
        ]
    )
    pulls = pr_health.fetch_pulls("acme/thing")

    comment_lookups = [a for a in seen if "comments" in " ".join(a)]
    assert len(comment_lookups) == 1
    assert "issues/1/comments" in " ".join(comment_lookups[0])
    assert pulls[0]["explained"] is False


def test_an_existing_marker_means_it_was_already_explained(calls):
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "CONFLICTING")])
    answers["comments"] = f"some other comment\n{MARKER}\nbody"
    pulls = pr_health.fetch_pulls("acme/thing")
    assert pulls[0]["explained"] is True


def test_apply_labels_then_comments(calls):
    seen, _ = calls
    pr_health.apply("acme/thing", [{"number": 7, "action": "add", "comment": True}])
    joined = [" ".join(a) for a in seen]
    assert any("--add-label conflicted" in c for c in joined)
    assert any(c.startswith("pr comment 7") for c in joined)


def test_apply_removing_a_label_does_not_comment(calls):
    seen, _ = calls
    pr_health.apply("acme/thing", [{"number": 7, "action": "remove", "comment": False}])
    joined = [" ".join(a) for a in seen]
    assert any("--remove-label conflicted" in c for c in joined)
    assert not any(c.startswith("pr comment") for c in joined)


def test_dry_run_changes_nothing(calls, capsys):
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "CONFLICTING")])
    assert pr_health.main(["--repo", "acme/thing", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would add conflicted on #1" in out
    assert not any("pr edit" in " ".join(a) for a in seen)


def test_no_actions_means_the_label_is_not_even_created(calls):
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "MERGEABLE")])
    assert pr_health.main(["--repo", "acme/thing"]) == 0
    assert not any("label create" in " ".join(a) for a in seen)


def test_one_failing_pull_request_does_not_drop_the_others(monkeypatch):
    """The whole point is that a missing signal gets noticed. Bailing on the
    first failure would lose the rest of the signals."""
    touched: list[str] = []

    def fake(args, *, check=True):
        if args[0] == "pr" and args[1] == "edit":
            number = args[2]
            if number == "1":
                raise RuntimeError("no permission")
            touched.append(number)
        return ""

    monkeypatch.setattr(pr_health, "_gh", fake)
    failed = pr_health.apply(
        "acme/thing",
        [
            {"number": 1, "action": "add", "comment": False},
            {"number": 2, "action": "add", "comment": False},
            {"number": 3, "action": "remove", "comment": False},
        ],
    )
    assert failed == 1
    assert touched == ["2", "3"]


def test_a_failure_still_makes_the_run_fail(calls, monkeypatch):
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "CONFLICTING")])
    monkeypatch.setattr(pr_health, "apply", lambda repo, actions: 1)
    assert pr_health.main(["--repo", "acme/thing"]) == 1


def test_a_clean_run_succeeds(calls):
    seen, answers = calls
    answers["pr list"] = json.dumps([_pr(1, "CONFLICTING")])
    assert pr_health.main(["--repo", "acme/thing"]) == 0


def test_the_comment_explains_that_checks_are_skipped_not_failed():
    """The whole point. A generic "please rebase" would bury it."""
    assert "skips" in pr_health.COMMENT
    assert "never ran" in pr_health.COMMENT
    assert MARKER in pr_health.COMMENT
