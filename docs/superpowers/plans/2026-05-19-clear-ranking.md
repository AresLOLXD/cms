# clear-ranking.sh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `clear-ranking.sh` script that interactively wipes ranking data from the production Docker container and optionally regenerates it from the CMS database.

**Architecture:** A single bash script at the repo root that sources `docker/_lib.sh` (provides `COMPOSE_CMD` and `ask_yes_no`), asks four yes/no questions, then issues `docker compose exec` commands into the running `cms` container to stop the ranking process, delete selected JSON files, restart the process, and optionally restart `cmsproxyservice` for regeneration. Documentation is added to `docs/docker-scripts.md`.

**Tech Stack:** bash, docker compose exec, supervisorctl (inside container)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `clear-ranking.sh` | Create | Interactive script — questions, guards, delete, restart |
| `docs/docker-scripts.md` | Modify | Add `clear-ranking.sh` to Scripts reference section |

---

### Task 1: Create clear-ranking.sh

**Files:**
- Create: `clear-ranking.sh`

> Note: This script requires a running Docker container to test. There are no unit-testable pure functions. Verification is manual.

- [ ] **Step 1: Create the script**

Create `clear-ranking.sh` at the repo root with this exact content:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

RANKING_LIB="/home/cmsuser/cms/lib/ranking"

CLEAR_RESULTS=false
CLEAR_USERS=false
CLEAR_CONTESTS=false
REGENERATE=false

if ask_yes_no "Clear results? (submissions and subchanges)" "n"; then CLEAR_RESULTS=true; fi
if ask_yes_no "Clear users?" "n"; then CLEAR_USERS=true; fi
if ask_yes_no "Clear tasks and contests?" "n"; then CLEAR_CONTESTS=true; fi
if ask_yes_no "Regenerate ranking from current contest data?" "n"; then REGENERATE=true; fi

if ! $CLEAR_RESULTS && ! $CLEAR_USERS && ! $CLEAR_CONTESTS && ! $REGENERATE; then
  echo "Nothing selected, exiting."
  exit 0
fi

if $CLEAR_CONTESTS && ! $CLEAR_RESULTS; then
  echo "Warning: clearing tasks and contests without clearing results will leave"
  echo "orphaned submission records in the ranking. Consider also clearing results."
fi

if $CLEAR_RESULTS || $CLEAR_USERS || $CLEAR_CONTESTS; then
  echo "Stopping ranking server..."
  "${COMPOSE_CMD[@]}" exec cms supervisorctl stop cmsrankingwebserver

  DELETE_CMD="rm -f"
  if $CLEAR_RESULTS; then
    DELETE_CMD="$DELETE_CMD $RANKING_LIB/submissions/*.json $RANKING_LIB/subchanges/*.json"
  fi
  if $CLEAR_USERS; then
    DELETE_CMD="$DELETE_CMD $RANKING_LIB/users/*.json"
  fi
  if $CLEAR_CONTESTS; then
    DELETE_CMD="$DELETE_CMD $RANKING_LIB/tasks/*.json $RANKING_LIB/contests/*.json"
  fi
  "${COMPOSE_CMD[@]}" exec cms sh -c "$DELETE_CMD"

  echo "Starting ranking server..."
  "${COMPOSE_CMD[@]}" exec cms supervisorctl start cmsrankingwebserver
fi

if $REGENERATE; then
  echo "Restarting proxy service..."
  "${COMPOSE_CMD[@]}" exec cms supervisorctl restart cmsproxyservice
  echo "Scores will appear in the ranking within ~6 minutes."
fi
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x clear-ranking.sh
```

- [ ] **Step 3: Verify shellcheck passes**

```bash
shellcheck clear-ranking.sh
```

Expected: no output (no warnings or errors).

- [ ] **Step 4: Commit**

```bash
git add clear-ranking.sh
git commit -m "feat: add clear-ranking.sh to wipe and regenerate ranking data"
```

---

### Task 2: Document clear-ranking.sh

**Files:**
- Modify: `docs/docker-scripts.md` (insert after the `contest.sh` section, before `## Configuring the project name`)

- [ ] **Step 1: Add the section to docker-scripts.md**

In `docs/docker-scripts.md`, insert this block after the `contest.sh` section (after line 106, before `## Configuring the project name`):

```markdown

### clear-ranking.sh

Clears ranking data from the running container. Asks what to delete (results, users, tasks/contests) and whether to regenerate the ranking from the current contest data in the database. Only affects the scoreboard — contestant submissions and scores stored in PostgreSQL are never touched.

```bash
./clear-ranking.sh
```

If you choose to regenerate, `ProxyService` is restarted and will re-push all scored submissions to the ranking. Scores appear on the scoreboard within ~6 minutes.
```

- [ ] **Step 2: Verify the file renders correctly**

```bash
grep -A 12 "### clear-ranking.sh" docs/docker-scripts.md
```

Expected output — the section header, description paragraph, code block, and regenerate note:

```
### clear-ranking.sh

Clears ranking data from the running container...
```

- [ ] **Step 3: Commit**

```bash
git add docs/docker-scripts.md
git commit -m "docs: add clear-ranking.sh to docker-scripts reference"
```
