#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_REMOTE="upstream"
UPSTREAM_BRANCH="main"
LOCAL_BRANCH="main"
ORIGIN_REMOTE="origin"

current_branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$current_branch" != "$LOCAL_BRANCH" ]]; then
  echo "Error: must be on '$LOCAL_BRANCH' branch (currently on '$current_branch')." >&2
  exit 1
fi

if ! git remote get-url "$UPSTREAM_REMOTE" &>/dev/null; then
  echo "Error: remote '$UPSTREAM_REMOTE' not found. Add it with:" >&2
  echo "  git remote add upstream https://github.com/cms-dev/cms.git" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Error: working tree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

echo "Fetching from $UPSTREAM_REMOTE..."
git fetch "$UPSTREAM_REMOTE"

local_sha=$(git rev-parse HEAD)
upstream_sha=$(git rev-parse "$UPSTREAM_REMOTE/$UPSTREAM_BRANCH")

if [[ "$local_sha" == "$upstream_sha" ]]; then
  echo "Already up to date."
  exit 0
fi

before_sha=$(git rev-parse HEAD)

echo "Merging $UPSTREAM_REMOTE/$UPSTREAM_BRANCH..."
if ! git merge "$UPSTREAM_REMOTE/$UPSTREAM_BRANCH" --no-edit; then
  git merge --abort 2>/dev/null || true
  echo "" >&2
  echo "Error: merge conflicts detected. Aborted." >&2
  echo "To sync manually:" >&2
  echo "  git merge $UPSTREAM_REMOTE/$UPSTREAM_BRANCH" >&2
  echo "  # resolve conflicts, then:" >&2
  echo "  git push $ORIGIN_REMOTE $LOCAL_BRANCH" >&2
  exit 1
fi

commit_count=$(git rev-list "$before_sha"..HEAD --count)

echo "Pushing to $ORIGIN_REMOTE/$LOCAL_BRANCH..."
git push "$ORIGIN_REMOTE" "$LOCAL_BRANCH"

echo "Done. Pulled $commit_count commit(s) from upstream."
