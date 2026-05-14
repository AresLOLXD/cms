# Docker Management Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create six root-level bash scripts (`up.sh`, `down.sh`, `status.sh`, `logs.sh`, `restart.sh`, `contest.sh`) backed by a shared `docker/_lib.sh` library, replacing the long manual `docker compose` command.

**Architecture:** A shared library (`docker/_lib.sh`) holds all common variables and helper functions; every script sources it. Scripts live at the repo root for easy discovery. Configuration (`CMS_PROJECT_NAME`) comes from `.env` via grep fallback so docker-compose variables and shell variables stay in sync.

**Tech Stack:** Bash 5+, Docker Compose v2, PostgreSQL (psql via docker exec), sed for `.env` edits.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docker/_lib.sh` | Create | Shared vars (`PROJECT_NAME`, `COMPOSE_CMD`) + `ask_yes_no` |
| `docker/test_lib.sh` | Create | Automated tests for `_lib.sh` |
| `up.sh` | Create | Start services (prompts for local DB + rebuild) |
| `down.sh` | Create | Stop services |
| `status.sh` | Create | Show container state |
| `logs.sh` | Create | Follow logs |
| `restart.sh` | Create | down + up |
| `contest.sh` | Create | Query DB, update `CMS_CONTEST_ID` in `.env`, optional restart |
| `.env.example` | Modify | Add `CMS_PROJECT_NAME` variable with docs |
| `docs/docker-scripts.md` | Create | User-facing guide (Docker-naive audience) |

---

## Task 1: `docker/_lib.sh` + automated tests

**Agent:** `voltagent-dev-exp:cli-developer`

**Files:**
- Create: `docker/_lib.sh`
- Create: `docker/test_lib.sh`

- [ ] **Step 1: Write the failing tests first**

Create `docker/test_lib.sh`:

```bash
#!/usr/bin/env bash
# Automated smoke tests for docker/_lib.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

check() {
  local desc="$1" result="$2"
  if [[ "$result" == "ok" ]]; then
    echo "  PASS: $desc"
    ((PASS++))
  else
    echo "  FAIL: $desc — $result"
    ((FAIL++))
  fi
}

echo "=== _lib.sh tests ==="

# ── ask_yes_no ────────────────────────────────────────────────────────────────

if echo "y" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' n" >/dev/null 2>&1; then
  check "ask_yes_no: 'y' returns 0" "ok"
else
  check "ask_yes_no: 'y' returns 0" "returned non-zero"
fi

if echo "n" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' y" >/dev/null 2>&1; then
  check "ask_yes_no: 'n' returns 1" "returned zero (expected non-zero)"
else
  check "ask_yes_no: 'n' returns 1" "ok"
fi

if echo "" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' n" >/dev/null 2>&1; then
  check "ask_yes_no: empty input uses default 'n' (returns 1)" "returned zero"
else
  check "ask_yes_no: empty input uses default 'n' (returns 1)" "ok"
fi

if echo "" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' y" >/dev/null 2>&1; then
  check "ask_yes_no: empty input uses default 'y' (returns 0)" "ok"
else
  check "ask_yes_no: empty input uses default 'y' (returns 0)" "returned non-zero"
fi

# ── PROJECT_NAME ──────────────────────────────────────────────────────────────

result=$(CMS_PROJECT_NAME="" bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$PROJECT_NAME")
if [[ "$result" == "cms-prod" ]]; then
  check "PROJECT_NAME defaults to 'cms-prod'" "ok"
else
  check "PROJECT_NAME defaults to 'cms-prod'" "got '$result'"
fi

result=$(CMS_PROJECT_NAME="my-contest" bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$PROJECT_NAME")
if [[ "$result" == "my-contest" ]]; then
  check "PROJECT_NAME reads CMS_PROJECT_NAME from environment" "ok"
else
  check "PROJECT_NAME reads CMS_PROJECT_NAME from environment" "got '$result'"
fi

# ── COMPOSE_CMD ───────────────────────────────────────────────────────────────

result=$(bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$COMPOSE_CMD")
if [[ "$result" == *"docker compose"* && "$result" == *"docker-compose.prod.yml"* ]]; then
  check "COMPOSE_CMD contains 'docker compose' and compose file path" "ok"
else
  check "COMPOSE_CMD contains 'docker compose' and compose file path" "got '$result'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
```

- [ ] **Step 2: Run tests to confirm they all fail (file not found)**

```bash
bash docker/test_lib.sh
```

Expected: `docker/_lib.sh: No such file or directory` errors — all tests fail.

- [ ] **Step 3: Create `docker/_lib.sh`**

```bash
#!/usr/bin/env bash
# docker/_lib.sh — shared variables and helpers. Source this; do not run directly.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read a variable: env takes priority, then .env file, then the given default.
_env_var() {
  local name="$1" default="$2"
  local val="${!name:-}"
  if [[ -z "$val" && -f "$REPO_ROOT/.env" ]]; then
    val=$(grep -m1 -E "^${name}=" "$REPO_ROOT/.env" | cut -d= -f2-)
  fi
  echo "${val:-$default}"
}

PROJECT_NAME="$(_env_var CMS_PROJECT_NAME cms-prod)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.prod.yml"
COMPOSE_CMD="docker compose -f $COMPOSE_FILE --env-file $REPO_ROOT/.env -p $PROJECT_NAME"

# ask_yes_no "Question text?" "y|n"
# Prints an interactive prompt. Returns 0 for yes, 1 for no.
ask_yes_no() {
  local question="$1" default="${2:-n}" prompt answer
  [[ "$default" == "y" ]] && prompt="[Y/n]" || prompt="[y/N]"
  while true; do
    read -r -p "$question $prompt " answer
    answer="${answer:-$default}"
    case "${answer,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}
```

- [ ] **Step 4: Make test script executable and run tests**

```bash
chmod +x docker/test_lib.sh
bash docker/test_lib.sh
```

Expected output:
```
=== _lib.sh tests ===
  PASS: ask_yes_no: 'y' returns 0
  PASS: ask_yes_no: 'n' returns 1
  PASS: ask_yes_no: empty input uses default 'n' (returns 1)
  PASS: ask_yes_no: empty input uses default 'y' (returns 0)
  PASS: PROJECT_NAME defaults to 'cms-prod'
  PASS: PROJECT_NAME reads CMS_PROJECT_NAME from environment
  PASS: COMPOSE_CMD contains 'docker compose' and compose file path

Results: 7 passed, 0 failed
```

- [ ] **Step 5: Commit**

```bash
git add docker/_lib.sh docker/test_lib.sh
git commit -m "feat: add docker/_lib.sh shared library with tests"
```

---

## Task 2: `up.sh`, `down.sh`, `status.sh`, `logs.sh`

**Agent:** `voltagent-dev-exp:cli-developer`

**Files:**
- Create: `up.sh`
- Create: `down.sh`
- Create: `status.sh`
- Create: `logs.sh`

- [ ] **Step 1: Create `up.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

ARGS=""

if ask_yes_no "Use local database (Docker)?" "n"; then
  ARGS="--profile localdb"
fi

BUILD_FLAG=""
if ask_yes_no "Rebuild image?" "n"; then
  BUILD_FLAG="--build"
fi

# shellcheck disable=SC2086
$COMPOSE_CMD up -d $ARGS $BUILD_FLAG
```

- [ ] **Step 2: Create `down.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

$COMPOSE_CMD down
```

- [ ] **Step 3: Create `status.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

$COMPOSE_CMD ps
```

- [ ] **Step 4: Create `logs.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

$COMPOSE_CMD logs -f
```

- [ ] **Step 5: Make all scripts executable**

```bash
chmod +x up.sh down.sh status.sh logs.sh
```

- [ ] **Step 6: Verify each script prints the right docker compose command (dry run)**

Run each script with `--dry-run` appended to COMPOSE_CMD temporarily is not easy, so instead verify the generated command looks correct by adding a debug echo before the actual run. Instead, just check that each script sources `_lib.sh` without error:

```bash
bash -n up.sh && echo "up.sh: syntax OK"
bash -n down.sh && echo "down.sh: syntax OK"
bash -n status.sh && echo "status.sh: syntax OK"
bash -n logs.sh && echo "logs.sh: syntax OK"
```

Expected: four lines ending in `syntax OK`.

- [ ] **Step 7: Commit**

```bash
git add up.sh down.sh status.sh logs.sh
git commit -m "feat: add up/down/status/logs wrapper scripts"
```

---

## Task 3: `restart.sh`

**Agent:** `voltagent-dev-exp:cli-developer`

**Files:**
- Create: `restart.sh`

- [ ] **Step 1: Create `restart.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

echo "Stopping services..."
$COMPOSE_CMD down

echo ""
echo "Starting services..."
bash "$SCRIPT_DIR/up.sh"
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x restart.sh
bash -n restart.sh && echo "restart.sh: syntax OK"
```

Expected: `restart.sh: syntax OK`

- [ ] **Step 3: Commit**

```bash
git add restart.sh
git commit -m "feat: add restart.sh"
```

---

## Task 4: `contest.sh`

**Agent:** `voltagent-dev-exp:cli-developer`

**Files:**
- Create: `contest.sh`
- Create: `docker/test_contest_env.sh`

- [ ] **Step 1: Write the failing test for the `.env` update logic**

Create `docker/test_contest_env.sh`:

```bash
#!/usr/bin/env bash
# Tests the sed logic used by contest.sh to update CMS_CONTEST_ID in .env
set -euo pipefail

PASS=0
FAIL=0

check() {
  local desc="$1" result="$2"
  if [[ "$result" == "ok" ]]; then
    echo "  PASS: $desc"
    ((PASS++))
  else
    echo "  FAIL: $desc — $result"
    ((FAIL++))
  fi
}

echo "=== contest.sh .env update tests ==="

TMPENV=$(mktemp)

# Test: update existing CMS_CONTEST_ID
echo "CMS_CONTEST_ID=1" > "$TMPENV"
sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=5/" "$TMPENV"
result=$(grep '^CMS_CONTEST_ID=' "$TMPENV" | cut -d= -f2)
if [[ "$result" == "5" ]]; then
  check "sed updates existing CMS_CONTEST_ID" "ok"
else
  check "sed updates existing CMS_CONTEST_ID" "got '$result'"
fi

# Test: append when CMS_CONTEST_ID is absent
echo "OTHER_VAR=foo" > "$TMPENV"
if ! grep -qE '^CMS_CONTEST_ID=' "$TMPENV"; then
  echo "CMS_CONTEST_ID=3" >> "$TMPENV"
fi
result=$(grep '^CMS_CONTEST_ID=' "$TMPENV" | cut -d= -f2)
if [[ "$result" == "3" ]]; then
  check "appends CMS_CONTEST_ID when not present" "ok"
else
  check "appends CMS_CONTEST_ID when not present" "got '$result'"
fi

# Test: only updates CMS_CONTEST_ID, leaves other vars intact
echo -e "CMS_PROJECT_NAME=cms-test\nCMS_CONTEST_ID=1\nCMS_DB_URL=foo" > "$TMPENV"
sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=7/" "$TMPENV"
project=$(grep '^CMS_PROJECT_NAME=' "$TMPENV" | cut -d= -f2)
db=$(grep '^CMS_DB_URL=' "$TMPENV" | cut -d= -f2)
if [[ "$project" == "cms-test" && "$db" == "foo" ]]; then
  check "other .env vars are not modified" "ok"
else
  check "other .env vars are not modified" "project='$project' db='$db'"
fi

rm -f "$TMPENV"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
```

- [ ] **Step 2: Run tests (they should pass immediately since we're testing sed, not contest.sh yet)**

```bash
bash docker/test_contest_env.sh
```

Expected:
```
=== contest.sh .env update tests ===
  PASS: sed updates existing CMS_CONTEST_ID
  PASS: appends CMS_CONTEST_ID when not present
  PASS: other .env vars are not modified

Results: 3 passed, 0 failed
```

- [ ] **Step 3: Create `contest.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env file not found at $ENV_FILE"
  echo "Copy .env.example to .env and fill in the required values first."
  exit 1
fi

# Show current contest ID
current_id=$(grep -m1 -E '^CMS_CONTEST_ID=' "$ENV_FILE" | cut -d= -f2 || echo "not set")
echo "Current contest ID: $current_id"
echo ""

# Try to list available contests from the running db container
ADMIN_PORT="$(_env_var CMS_AWS_HTTP_PORT 8889)"
DB_USER="$(_env_var POSTGRES_USER cms)"
DB_NAME="$(_env_var POSTGRES_DB cmsdb)"

echo "Fetching contests from database..."
CONTESTS=$(
  $COMPOSE_CMD exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
    -c "SELECT id, name FROM contests ORDER BY id;" \
    2>/dev/null
) || CONTESTS=""

if [[ -z "$CONTESTS" || "$CONTESTS" == *"(0 rows)"* ]]; then
  echo "No contests found in the database."
  echo "Create one from the Admin interface: http://localhost:${ADMIN_PORT}"
  echo ""
else
  echo "Available contests:"
  echo "$CONTESTS"
  echo ""
fi

# Prompt for new ID
while true; do
  read -r -p "Enter new contest ID: " new_id
  if [[ "$new_id" =~ ^[0-9]+$ && "$new_id" -gt 0 ]]; then
    break
  fi
  echo "Please enter a positive integer (e.g. 1, 2, 3)."
done

# Update .env
if grep -qE '^CMS_CONTEST_ID=' "$ENV_FILE"; then
  sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=${new_id}/" "$ENV_FILE"
else
  echo "CMS_CONTEST_ID=${new_id}" >> "$ENV_FILE"
fi

echo "Contest ID updated to ${new_id} in .env"

if ask_yes_no "Restart services to apply change?" "n"; then
  bash "$SCRIPT_DIR/restart.sh"
fi
```

- [ ] **Step 4: Make executable and verify syntax**

```bash
chmod +x contest.sh docker/test_contest_env.sh
bash -n contest.sh && echo "contest.sh: syntax OK"
```

Expected: `contest.sh: syntax OK`

- [ ] **Step 5: Commit**

```bash
git add contest.sh docker/test_contest_env.sh
git commit -m "feat: add contest.sh for switching active contest ID"
```

---

## Task 5: Update `.env.example` with `CMS_PROJECT_NAME`

**Agent:** main (claude)

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add `CMS_PROJECT_NAME` to `.env.example`**

Open `.env.example` and add a new section immediately after the `SCALING` block (after the `CMS_WORKER_COUNT=1` line, before the `# ---` line that starts `INTERNAL RPC PORTS`):

```
# -----------------------------------------------------------
# DOCKER PROJECT (optional — default shown)
# -----------------------------------------------------------

# Name used to group Docker containers for this deployment (-p flag).
# Change this if you run multiple CMS instances on the same machine.
CMS_PROJECT_NAME=cms-prod
```

- [ ] **Step 2: Verify the file still parses (no syntax errors)**

```bash
grep -c '=' .env.example
```

Expected: a number greater than 15 (just checking the file has content).

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "feat: add CMS_PROJECT_NAME to .env.example"
```

---

## Task 6: `docs/docker-scripts.md`

**Agent:** `voltagent-dev-exp:documentation-engineer`

**Files:**
- Create: `docs/docker-scripts.md`

**Audience brief for the agent:** Users who know basic Linux (run a script, edit a file, use a terminal) but have never used Docker. Avoid Docker jargon without a plain-language explanation. Lead with what each script *does* before showing the command. Troubleshooting uses symptom-first language ("If nothing loads in the browser…") not error-code-first.

- [ ] **Step 1: Create `docs/docker-scripts.md`**

The document must include:

1. **Introduction** — What these scripts do and why they exist (replaces a long manual command). One paragraph, no Docker assumed.

2. **Before you start** — Two prerequisites:
   - Docker is installed (link to `docker.com/get-docker`)
   - `.env` file exists (copy from `.env.example`, brief mention)

3. **Quick start** — Three steps: copy `.env`, run `./up.sh`, open browser. Show the exact prompts the user will see.

4. **Scripts reference** — One subsection per script. For each: one plain-English sentence describing what it does, then the command.
   - `./up.sh` — starts everything; explains the two prompts
   - `./down.sh` — stops everything
   - `./status.sh` — shows whether containers are running
   - `./logs.sh` — live log output; explain Ctrl+C
   - `./restart.sh` — down + up in sequence
   - `./contest.sh` — explains what a "contest ID" is and when you'd change it

5. **Configuring the project name** — Explains `CMS_PROJECT_NAME` in `.env`, when you'd change it (running two instances on the same machine), and what the default is.

6. **Troubleshooting** — Symptom-first, three entries:
   - "The browser shows nothing / connection refused" → service still starting, check `./status.sh` and `./logs.sh`
   - "No contests appear in `./contest.sh`" → import a contest first via Admin UI
   - "Permission denied running a script" → `chmod +x <script>`

- [ ] **Step 2: Verify the file was created**

```bash
wc -l docs/docker-scripts.md
```

Expected: more than 50 lines.

- [ ] **Step 3: Commit**

```bash
git add docs/docker-scripts.md
git commit -m "docs: add docker-scripts.md user guide"
```

---

## Final verification

- [ ] **Run all automated tests**

```bash
bash docker/test_lib.sh && bash docker/test_contest_env.sh
```

Expected: all tests pass, `0 failed`.

- [ ] **Verify all scripts are executable**

```bash
ls -la up.sh down.sh status.sh logs.sh restart.sh contest.sh
```

Expected: `-rwxr-xr-x` permissions on all six files.

- [ ] **Verify syntax of all scripts**

```bash
for f in up.sh down.sh status.sh logs.sh restart.sh contest.sh docker/_lib.sh; do
  bash -n "$f" && echo "$f: OK"
done
```

Expected: seven lines ending in `OK`.
