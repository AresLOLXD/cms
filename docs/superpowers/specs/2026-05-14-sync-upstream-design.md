# Design: sync-upstream.sh

**Date:** 2026-05-14  
**Status:** Approved

## Goal

A standalone bash script that fetches changes from the upstream CMS repository (`cms-dev/cms`) and merges them into the local `main` branch, then pushes to `origin`. If there are conflicts, it aborts cleanly and notifies the user.

## Script location

`sync-upstream.sh` — in the repository root, alongside `up.sh`, `down.sh`, etc.

## Flow

1. **Precondition checks** (bail early, touch nothing if these fail):
   - Current branch must be `main`. If not, print an error and exit 1.
   - Working tree must be clean (no staged or unstaged changes). If dirty, print an error and exit 1.
   - Remote `upstream` must exist. If not, print an error and exit 1.

2. **Fetch** — `git fetch upstream`

3. **Check if up to date** — compare `HEAD` with `upstream/main`. If identical, print "Already up to date." and exit 0.

4. **Merge** — `git merge upstream/main --no-edit`
   - On conflict: run `git merge --abort`, print a clear message listing what to do next, exit 1.

5. **Push** — `git push origin main`

6. **Success message** — print how many commits were pulled in.

## Error handling

| Situation | Behavior |
|---|---|
| Not on `main` | Exit 1, no git operations performed |
| Dirty working tree | Exit 1, no git operations performed |
| `upstream` remote missing | Exit 1, no git operations performed |
| Merge conflicts | `git merge --abort`, exit 1, instruct user to merge manually |
| Push fails | Script exits via `set -e`, user sees git's error output |

## Implementation details

- `#!/usr/bin/env bash` + `set -euo pipefail`
- No external dependencies beyond `git`
- No `--dry-run` flag (out of scope)
- No `docker/_lib.sh` sourced (git-only script, no docker concerns)
- Upstream branch assumed to be `upstream/main`

## Out of scope

- Rebase strategy
- Interactive conflict resolution
- Dry-run mode
- Syncing branches other than `main`
