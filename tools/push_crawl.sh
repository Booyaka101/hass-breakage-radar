#!/usr/bin/env bash
# Commit whatever the crawl has staged and push it onto a main that moved.
#
# A slice takes about ten minutes and the lookups longer, so main can gain a
# merge while a run is going. The files a crawl writes are generated, not
# authored, so its own side is always the right answer for them, and rebase
# calls that side theirs: ours is the branch being replayed onto, origin/main.
set -euo pipefail

message="$1"

# The step that saves a run's progress stages nothing when the step above it
# already committed and only the push failed, and that commit is the progress
# this script is here to land. Being ahead of the branch is work too.
ahead=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)
if git diff --cached --quiet; then
  if [ "${ahead}" -eq 0 ]; then
    echo "Nothing staged to commit."
    exit 0
  fi
  echo "Nothing staged; pushing ${ahead} commit(s) an earlier attempt left behind"
else
  git commit -m "${message}"
fi

for attempt in 1 2 3; do
  if git push; then
    exit 0
  fi
  echo "push rejected (attempt ${attempt}); rebasing onto the updated main"
  git fetch origin main
  # data/rules.json is generated too, but from manual_rules.json and the blog,
  # and this run generated it before whatever just landed. Keeping our side of
  # it would drop a rule somebody merged mid-run until tomorrow's crawl derives
  # it again, so main wins that one file and the crawl's own output wins the
  # rest.
  base=$(git merge-base HEAD origin/main)
  if ! git diff --quiet "${base}" HEAD -- data/rules.json &&
     ! git diff --quiet "${base}" origin/main -- data/rules.json; then
    echo "main moved data/rules.json during the run; keeping its copy"
    git checkout origin/main -- data/rules.json
    git commit --quiet --amend --no-edit --allow-empty
  fi
  # An abort that fails is a rebase that never started, which is still a
  # rebase that did not happen, and letting set -e take that one exits the
  # script with git's code and none of this explanation.
  if ! git rebase -X theirs origin/main; then
    git rebase --abort || true
    echo "could not rebase cleanly; leaving main alone"
    exit 1
  fi
  # The board is markup rendered from a template in tools/, so a run that
  # publishes it rendered it from the copy it started with, and taking the
  # crawl's side of the page drops a template change that landed mid-run.
  # Rendering again on top of what is now checked out puts both in the file.
  if ! git diff --quiet origin/main HEAD -- docs/index.html; then
    # A render that fails leaves the page this run built, which is what the
    # push was about to carry anyway. Half of a written one cannot stay in the
    # tree, though: the next attempt's rebase refuses to start on it.
    if python tools/build_index.py >/dev/null; then
      git add docs/index.json docs/index.html docs/feed.xml docs/feed.xsl state/feed.json
      git diff --cached --quiet || git commit --quiet --amend --no-edit
    else
      git checkout -- docs state
    fi
  fi
done
echo "still could not push after three attempts"
exit 1
