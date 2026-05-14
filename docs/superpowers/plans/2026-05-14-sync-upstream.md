# sync-upstream.sh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `sync-upstream.sh`, a script that fetches changes from `cms-dev/cms`, merges them into local `main`, and pushes to `origin` — aborting cleanly on conflicts.

**Architecture:** Single standalone bash script in the repo root. No external dependencies beyond `git`. Runs precondition checks first (branch, cleanliness, remote existence), then fetch → merge → push, with conflict detection via `if !` guard to stay compatible with `set -euo pipefail`.

**Tech Stack:** bash, git

---

## File Map

| Action | Path |
|---|---|
| Create | `sync-upstream.sh` |

---

### Task 1: Write precondition checks

**Files:**
- Create: `sync-upstream.sh`

- [ ] **Step 1: Create the script with header and precondition checks**

Create `/path/to/repo/sync-upstream.sh` with this exact content:

```bash
#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_REMOTE="upstream"
UPSTREAM_BRANCH="upstream/main"
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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x sync-upstream.sh
```

- [ ] **Step 3: Verify precondition — wrong branch**

Temporarily switch to another branch and run the script:

```bash
git checkout -b test-branch
./sync-upstream.sh
```

Expected output:
```
Error: must be on 'main' branch (currently on 'test-branch').
```
Exit code should be 1:
```bash
echo $?   # → 1
```

- [ ] **Step 4: Return to main**

```bash
git checkout main
git branch -d test-branch
```

- [ ] **Step 5: Verify precondition — missing remote**

Temporarily remove the upstream remote and run the script:

```bash
git remote remove upstream
./sync-upstream.sh
```

Expected output:
```
Error: remote 'upstream' not found. Add it with:
  git remote add upstream https://github.com/cms-dev/cms.git
```

- [ ] **Step 6: Restore upstream remote**

```bash
git remote add upstream git@github.com:cms-dev/cms.git
```

- [ ] **Step 7: Verify precondition — dirty working tree**

Create an uncommitted change and run the script:

```bash
echo "dirty" >> /tmp/dirty_test_file && cp /tmp/dirty_test_file dirty_test_file
./sync-upstream.sh
```

Expected output:
```
Error: working tree has uncommitted changes. Commit or stash them first.
```

Clean up:
```bash
rm dirty_test_file
```

---

### Task 2: Add fetch, up-to-date check, merge, push, and success message

**Files:**
- Modify: `sync-upstream.sh`

- [ ] **Step 1: Append the fetch + up-to-date check + merge + push block**

Add this content after the precondition checks block (at the end of the file):

```bash
echo "Fetching from $UPSTREAM_REMOTE..."
git fetch "$UPSTREAM_REMOTE"

local_sha=$(git rev-parse HEAD)
upstream_sha=$(git rev-parse "$UPSTREAM_BRANCH")

if [[ "$local_sha" == "$upstream_sha" ]]; then
  echo "Already up to date."
  exit 0
fi

commit_count=$(git rev-list HEAD.."$UPSTREAM_BRANCH" --count)

echo "Merging $UPSTREAM_BRANCH ($commit_count new commit(s))..."
if ! git merge "$UPSTREAM_BRANCH" --no-edit; then
  git merge --abort
  echo "" >&2
  echo "Error: merge conflicts detected. Aborted." >&2
  echo "To sync manually:" >&2
  echo "  git merge $UPSTREAM_BRANCH" >&2
  echo "  # resolve conflicts, then:" >&2
  echo "  git push $ORIGIN_REMOTE $LOCAL_BRANCH" >&2
  exit 1
fi

echo "Pushing to $ORIGIN_REMOTE/$LOCAL_BRANCH..."
git push "$ORIGIN_REMOTE" "$LOCAL_BRANCH"

echo "Done. Pulled $commit_count commit(s) from upstream."
```

- [ ] **Step 2: Verify the full script looks correct**

```bash
cat sync-upstream.sh
```

The file should look like this in its entirety:

```bash
#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_REMOTE="upstream"
UPSTREAM_BRANCH="upstream/main"
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
upstream_sha=$(git rev-parse "$UPSTREAM_BRANCH")

if [[ "$local_sha" == "$upstream_sha" ]]; then
  echo "Already up to date."
  exit 0
fi

commit_count=$(git rev-list HEAD.."$UPSTREAM_BRANCH" --count)

echo "Merging $UPSTREAM_BRANCH ($commit_count new commit(s))..."
if ! git merge "$UPSTREAM_BRANCH" --no-edit; then
  git merge --abort
  echo "" >&2
  echo "Error: merge conflicts detected. Aborted." >&2
  echo "To sync manually:" >&2
  echo "  git merge $UPSTREAM_BRANCH" >&2
  echo "  # resolve conflicts, then:" >&2
  echo "  git push $ORIGIN_REMOTE $LOCAL_BRANCH" >&2
  exit 1
fi

echo "Pushing to $ORIGIN_REMOTE/$LOCAL_BRANCH..."
git push "$ORIGIN_REMOTE" "$LOCAL_BRANCH"

echo "Done. Pulled $commit_count commit(s) from upstream."
```

- [ ] **Step 3: Run the script for real**

```bash
./sync-upstream.sh
```

Expected output (if already up to date):
```
Fetching from upstream...
Already up to date.
```

Expected output (if there are new commits):
```
Fetching from upstream...
Merging upstream/main (N new commit(s))...
Pushing to origin/main...
Done. Pulled N commit(s) from upstream.
```

- [ ] **Step 4: Commit**

```bash
git add sync-upstream.sh
git commit -m "feat: add sync-upstream.sh to pull changes from cms-dev/cms"
```
