#!/usr/bin/env bash
# Commit whatever the crawl has staged and push it onto a main that moved.
#
# A slice takes about ten minutes and the lookups longer, so main can gain a
# merge while a run is going. The files a crawl writes are generated, not
# authored, so its own side is always the right answer for them, and rebase
# calls that side theirs: ours is the branch being replayed onto, origin/main.
set -euo pipefail

message="$1"

if git diff --cached --quiet; then
  echo "Nothing staged to commit."
  exit 0
fi
git commit -m "${message}"

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
  git rebase -X theirs origin/main || {
    git rebase --abort
    echo "could not rebase cleanly; leaving main alone"
    exit 1
  }
done
echo "still could not push after three attempts"
exit 1
