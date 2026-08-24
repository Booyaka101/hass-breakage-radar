"""The GitHub Action is a wrapper, so the thing to test is that it still fits.

An action that passes a flag the CLI dropped fails in somebody else's CI, on
their pull request, with an argparse traceback. These assertions are cheap and
that failure is not.
"""

from __future__ import annotations

import re

import pytest

from tools.check_local import build_parser

yaml = pytest.importorskip("yaml", reason="PyYAML is not a runtime dependency")


@pytest.fixture
def action(repo_root):
    return yaml.safe_load((repo_root / "action.yml").read_text(encoding="utf-8"))


@pytest.fixture
def scan_step(action):
    steps = action["runs"]["steps"]
    return next(step for step in steps if "run" in step)


def test_every_flag_the_action_passes_still_exists(scan_step):
    known = {
        option
        for act in build_parser()._actions
        for option in act.option_strings
    }
    used = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", scan_step["run"]))
    assert used <= known, f"action.yml passes flags the CLI does not accept: {used - known}"


@pytest.mark.parametrize(
    ("field", "flag"),
    [("fail-on", "--fail-on"), ("path", None)],
)
def test_declared_defaults_are_values_the_cli_accepts(action, field, flag):
    default = action["inputs"][field]["default"]
    if flag is None:
        return
    choices = next(
        act.choices for act in build_parser()._actions if flag in act.option_strings
    )
    assert default in choices


def test_the_default_is_not_a_release_gate(action):
    """A deprecation a year out failing an unrelated pull request is how this
    gets uninstalled. The CLI defaults to `any`; the action must not."""
    assert action["inputs"]["fail-on"]["default"] == "never"


def test_pinned_rules_point_at_a_file_that_ships(repo_root, scan_step):
    assert "$RADAR_ACTION_PATH/data/rules.json" in scan_step["run"]
    assert (repo_root / "data" / "rules.json").is_file()


def test_the_scan_step_reaches_inputs_through_the_environment(scan_step):
    """Interpolating ${{ inputs.x }} straight into a shell line lets a branch
    name run as code. Every input goes through env instead."""
    assert "${{" not in scan_step["run"]
    assert set(scan_step["env"]) >= {"RADAR_PATH", "RADAR_FAIL_ON", "RADAR_RULES"}


def test_the_action_declares_every_input_it_uses(action, scan_step):
    used = set(re.findall(r"\$\{\{\s*inputs\.([a-z-]+)\s*\}\}", yaml.dump(action)))
    assert used <= set(action["inputs"])


def test_it_is_a_composite_action_with_no_container_pull(action):
    assert action["runs"]["using"] == "composite"
