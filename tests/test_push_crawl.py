"""What tools/push_crawl.sh does when main moved under a running crawl.

The crawl pushes generated files, so it takes its own side of a conflict. Two
of the things it generates are not generated from its data alone, which is what
these drive: a miniature repository with the same shape, a stub renderer, and
the real script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = "<h1>Breakage Radar</h1>\n<p>the standfirst</p>\n<div>{stats}</div>\n"

RENDER = """import pathlib, sys
template = pathlib.Path("tools/template.html").read_text(encoding="utf-8")
stats = pathlib.Path("data/findings.json").read_text(encoding="utf-8").strip()
pathlib.Path("docs/index.html").write_text(
    template.replace("{stats}", stats), encoding="utf-8"
)
"""

def _runs_the_script(shell: Path) -> bool:
    """Whether this shell can run the script at all, asked by running it.

    On Windows a bare `bash` is the WSL launcher, which is a different machine
    with a different git, and on a box with a distro installed it answers
    every cheaper question convincingly.
    """
    if not shell.exists():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        shutil.copy(REPO_ROOT / "tools" / "push_crawl.sh", root / "tools")
        subprocess.run(
            ["git", "init", "-q", "-b", "main"], cwd=root, check=True, capture_output=True
        )
        done = subprocess.run(
            [str(shell), "tools/push_crawl.sh", "probe"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    return done.returncode == 0 and "Nothing staged" in done.stdout


def _shell() -> str | None:
    git_exe = shutil.which("git")
    if not git_exe:
        return None
    on_path = shutil.which("bash")
    candidates = [Path(git_exe).parents[1] / "bin" / "bash.exe"]
    candidates += [Path(on_path)] if on_path else []
    return next((str(c) for c in candidates if _runs_the_script(c)), None)


BASH = _shell()

pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and BASH), reason="needs git and a shell that runs it"
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def render(cwd):
    done = subprocess.run(
        ["python", "tools/build_index.py"], cwd=cwd, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def clone(origin, path):
    subprocess.run(
        ["git", "clone", "-q", "-b", "main", str(origin), str(path)],
        check=True,
        capture_output=True,
    )
    git(path, "config", "user.email", "crawl@example.invalid")
    git(path, "config", "user.name", "crawl")
    return path


@pytest.fixture
def crawl(tmp_path):
    """A repository shaped like this one, cloned twice off a shared origin."""
    base = tmp_path / "base"
    (base / "tools").mkdir(parents=True)
    (base / "docs").mkdir()
    (base / "data").mkdir()
    (base / "state").mkdir()
    (base / "tools" / "template.html").write_text(TEMPLATE, encoding="utf-8")
    (base / "tools" / "build_index.py").write_text(RENDER, encoding="utf-8")
    (base / "data" / "findings.json").write_text("100\n", encoding="utf-8")
    (base / "data" / "rules.json").write_text("{}\n", encoding="utf-8")
    for name in ("index.json", "feed.xml", "feed.xsl"):
        (base / "docs" / name).write_text("", encoding="utf-8")
    (base / "state" / "feed.json").write_text("", encoding="utf-8")
    shutil.copy(REPO_ROOT / "tools" / "push_crawl.sh", base / "tools")
    git(base, "init", "-q", "-b", "main")
    git(base, "config", "user.email", "crawl@example.invalid")
    git(base, "config", "user.name", "crawl")
    render(base)
    git(base, "add", "-A")
    git(base, "commit", "-qm", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(base), str(origin)],
        check=True,
        capture_output=True,
    )
    work = clone(origin, tmp_path / "work")

    # main moves after the run is under way, which is the whole point.
    other = clone(origin, tmp_path / "other")
    (other / "tools" / "template.html").write_text(
        TEMPLATE.replace("the standfirst", "the reworded standfirst"), encoding="utf-8"
    )
    render(other)
    git(other, "commit", "-qam", "board: reword the standfirst")
    git(other, "push", "-q")
    return work


def push(work, message="chore(crawl): refresh index"):
    return subprocess.run(
        # The resolved path, because a bare "bash" on Windows finds the WSL
        # launcher before the one git ships with.
        [BASH, "tools/push_crawl.sh", message],
        cwd=work,
        capture_output=True,
        text=True,
    )


def pushed(work, path):
    return git(work, "show", f"origin/main:{path}")


def test_a_board_change_that_landed_mid_crawl_survives_the_rebase(crawl):
    """The page is markup rendered from a template plus the crawl's numbers,
    and the run rendered it before the merge landed. Taking the crawl's side
    of the file is right for the numbers and reverts the markup."""
    (crawl / "data" / "findings.json").write_text("200\n", encoding="utf-8")
    render(crawl)
    git(crawl, "add", "data/findings.json", "docs/index.html")

    assert push(crawl).returncode == 0
    page = pushed(crawl, "docs/index.html")
    assert "the reworded standfirst" in page, "the merged board change was reverted"
    assert "<div>200</div>" in page, "the crawl's own numbers did not survive"


def test_a_render_that_fails_leaves_the_tree_the_rebase_needs(crawl):
    """The re-render is a nicety; the push is not. A half-written page has to
    go all the same, because the next attempt's rebase refuses to start on
    one."""
    (crawl / "data" / "findings.json").write_text("200\n", encoding="utf-8")
    render(crawl)
    git(crawl, "add", "data/findings.json", "docs/index.html")
    (crawl / "tools" / "build_index.py").write_text(
        'import pathlib, sys\npathlib.Path("docs/index.json").write_text("half")\n'
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    git(crawl, "add", "tools/build_index.py")

    assert push(crawl).returncode == 0
    assert git(crawl, "status", "--porcelain") == "", "half a page left behind"
    assert "<div>200</div>" in pushed(crawl, "docs/index.html")


def test_a_rebase_that_cannot_start_says_so(crawl):
    """A rebase refuses on a dirty tree, and aborting one that never started
    fails too. Under set -e that second failure ends the script with git's
    exit code and none of the explanation."""
    (crawl / "data" / "findings.json").write_text("200\n", encoding="utf-8")
    git(crawl, "add", "data/findings.json")
    (crawl / "data" / "rules.json").write_text('{"dirty": 1}\n', encoding="utf-8")

    result = push(crawl)
    assert result.returncode == 1, result.stderr
    assert "could not rebase cleanly" in result.stdout
    assert json.loads((crawl / "data" / "rules.json").read_text()) == {"dirty": 1}
