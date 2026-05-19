# Infrastructure Scripts Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the six infrastructure management scripts and their shared library by adding localdb persistence, extracting `_do_up()`, adding confirmation guards, improving `status.sh` and `logs.sh`, and sharing `SUPERVISORCTL`.

**Architecture:** All changes flow through `docker/_lib.sh` first — it gains `_set_env_var`, `_do_up`, `SUPERVISORCTL`, localdb auto-append, and EOF fix. The individual scripts then become thin callers of these shared primitives. No new files are created.

**Tech Stack:** bash, shellcheck, docker compose v2

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `docker/_lib.sh` | Modify | Add `CMS_USE_LOCALDB` auto-append, `SUPERVISORCTL`, `_set_env_var`, `_do_up`, EOF fix on `ask_yes_no` |
| `up.sh` | Modify | Replace all inline logic with `_do_up` call |
| `down.sh` | Modify | Add confirmation guard |
| `restart.sh` | Modify | Add confirmation guard, replace `bash up.sh` with `_do_up`, add `compose ps` |
| `status.sh` | Modify | Add project name, contest ID, supervisord status |
| `logs.sh` | Modify | Add `--tail 100` default, `$@` passthrough, drop hardcoded `-f` |
| `clear-ranking.sh` | Modify | Remove local `SUPERVISORCTL` array (now in `_lib.sh`) |

---

### Task 1: Harden `docker/_lib.sh`

**Files:**
- Modify: `docker/_lib.sh`

> No unit tests for shell helpers — shellcheck is the verification gate. Manual smoke tests described in the step.

- [ ] **Step 1: Replace `docker/_lib.sh` with the hardened version**

Replace the entire file with:

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
    val="${val#\"}" ; val="${val%\"}"   # strip surrounding double-quotes if present
  fi
  echo "${val:-$default}"
}

PROJECT_NAME="$(_env_var CMS_PROJECT_NAME cms-prod)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.prod.yml"
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" -p "$PROJECT_NAME")

# Append --profile localdb if CMS_USE_LOCALDB=true is persisted in .env
if [[ "$(_env_var CMS_USE_LOCALDB false)" == "true" ]]; then
  COMPOSE_CMD+=(--profile localdb)
fi

SUPERVISORCTL=(supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf)

# ask_yes_no "Question text?" "y|n"
# Prints an interactive prompt. Returns 0 for yes, 1 for no.
ask_yes_no() {
  local question="$1" default="${2:-n}" prompt answer
  [[ "$default" == "y" ]] && prompt="[Y/n]" || prompt="[y/N]"
  while true; do
    read -r -p "$question $prompt " answer || break
    answer="${answer:-$default}"
    case "${answer,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
  [[ "${default,,}" == "y" ]] && return 0 || return 1
}

# _set_env_var KEY VALUE — atomically write or update KEY=VALUE in .env
_set_env_var() {
  local key="$1" value="$2"
  local env_file="$REPO_ROOT/.env"
  local tmp_file="${env_file}.tmp"
  if grep -qE "^${key}=" "$env_file" 2>/dev/null; then
    sed "s|^${key}=.*|${key}=${value}|" "$env_file" > "$tmp_file"
  else
    { cat "$env_file" 2>/dev/null; echo "${key}=${value}"; } > "$tmp_file"
  fi
  mv "$tmp_file" "$env_file"
}

# _do_up — Docker preflight, localdb choice (persisted), optional rebuild, compose up --wait
_do_up() {
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running or not accessible." >&2
    exit 1
  fi

  if ask_yes_no "Use local PostgreSQL container?" "n"; then
    _set_env_var "CMS_USE_LOCALDB" "true"
    COMPOSE_CMD+=(--profile localdb)
  else
    _set_env_var "CMS_USE_LOCALDB" "false"
  fi

  local up_args=()
  if ask_yes_no "Rebuild image?" "n"; then
    up_args+=(--build)
  fi

  "${COMPOSE_CMD[@]}" up -d --wait "${up_args[@]}"
}
```

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x docker/_lib.sh
```

Expected: no output (no warnings or errors).

- [ ] **Step 3: Smoke-test `_set_env_var` in isolation**

```bash
# Create a temp .env and verify the helper writes/updates correctly
tmp=$(mktemp)
echo "FOO=old" > "$tmp"

# Test update existing key
REPO_ROOT="$(dirname "$tmp")" bash -c '
  source docker/_lib.sh 2>/dev/null || true
  _set_env_var() {
    local key="$1" value="$2"
    local env_file="'"$tmp"'"
    local tmp_file="${env_file}.tmp"
    if grep -qE "^${key}=" "$env_file" 2>/dev/null; then
      sed "s|^${key}=.*|${key}=${value}|" "$env_file" > "$tmp_file"
    else
      { cat "$env_file" 2>/dev/null; echo "${key}=${value}"; } > "$tmp_file"
    fi
    mv "$tmp_file" "$env_file"
  }
  _set_env_var FOO new
  _set_env_var BAR added
  cat '"$tmp"
'
rm -f "$tmp"
```

Expected output:
```
FOO=new
BAR=added
```

- [ ] **Step 4: Commit**

```bash
git add docker/_lib.sh
git commit -m "feat: add _set_env_var, _do_up, SUPERVISORCTL, localdb persistence to _lib.sh"
```

---

### Task 2: Simplify `up.sh`

**Files:**
- Modify: `up.sh`

- [ ] **Step 1: Replace `up.sh` with thin wrapper**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

_do_up
```

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x up.sh
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add up.sh
git commit -m "refactor: up.sh delegates to _do_up in _lib.sh"
```

---

### Task 3: Add confirmation guard to `down.sh`

**Files:**
- Modify: `down.sh`

- [ ] **Step 1: Replace `down.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

if ! ask_yes_no "Stop all services? Contestants will be disconnected." "n"; then
  echo "Aborted."
  exit 0
fi

"${COMPOSE_CMD[@]}" down
```

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x down.sh
```

Expected: no output.

- [ ] **Step 3: Verify non-interactive default is safe**

Run with `n` piped as input and confirm it exits without stopping anything:

```bash
echo "n" | bash down.sh
```

Expected output:
```
Stop all services? Contestants will be disconnected. [y/N] Aborted.
```

- [ ] **Step 4: Commit**

```bash
git add down.sh
git commit -m "feat: add confirmation guard to down.sh"
```

---

### Task 4: Harden `restart.sh`

**Files:**
- Modify: `restart.sh`

- [ ] **Step 1: Replace `restart.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

if ! ask_yes_no "Restart all services?" "n"; then
  echo "Aborted."
  exit 0
fi

echo "Stopping services..."
"${COMPOSE_CMD[@]}" down

echo ""
echo "Starting services..."
_do_up

echo ""
"${COMPOSE_CMD[@]}" ps
```

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x restart.sh
```

Expected: no output.

- [ ] **Step 3: Verify abort path**

```bash
echo "n" | bash restart.sh
```

Expected output:
```
Restart all services? [y/N] Aborted.
```

- [ ] **Step 4: Commit**

```bash
git add restart.sh
git commit -m "feat: restart.sh uses _do_up, adds confirmation guard and post-up ps"
```

---

### Task 5: Improve `status.sh`

**Files:**
- Modify: `status.sh`

- [ ] **Step 1: Replace `status.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

echo "Project:    $(_env_var CMS_PROJECT_NAME cms-prod)"
echo "Contest ID: $(_env_var CMS_CONTEST_ID "(none)")"
echo ""

echo "── Container status ─────────────────────────────────"
"${COMPOSE_CMD[@]}" ps
echo ""

echo "── Supervisor status ────────────────────────────────"
"${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" status 2>/dev/null \
  || echo "(container not running)"
```

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x status.sh
```

Expected: no output.

- [ ] **Step 3: Verify degraded output when container is down**

```bash
# Temporarily test by calling status.sh when cms container is not running.
# Expected: shows "Project:" and "Contest ID:" lines, then compose ps output,
# then "(container not running)" instead of supervisor status.
./status.sh
```

Expected first two lines:
```
Project:    <value from .env or cms-prod>
Contest ID: <value from .env or (none)>
```

- [ ] **Step 4: Commit**

```bash
git add status.sh
git commit -m "feat: status.sh shows project name, contest ID, and supervisord status"
```

---

### Task 6: Improve `logs.sh`

**Files:**
- Modify: `logs.sh`

- [ ] **Step 1: Replace `logs.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

has_tail=false
for arg in "$@"; do
  [[ "$arg" == --tail* ]] && has_tail=true && break
done

tail_args=()
$has_tail || tail_args=(--tail 100)

"${COMPOSE_CMD[@]}" logs "${tail_args[@]}" "$@"
```

Note: the hardcoded `-f` (follow) is removed. Users who want to follow logs pass `-f`
explicitly: `./logs.sh -f`. The default now dumps the last 100 lines and exits, which
is safer on servers with large log histories.

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x logs.sh
```

Expected: no output.

- [ ] **Step 3: Verify `--tail` passthrough**

With the container running, confirm `--tail` override works:

```bash
./logs.sh --tail 5
```

Expected: shows the last 5 log lines across all containers, then exits.

```bash
./logs.sh
```

Expected: shows the last 100 log lines (default), then exits.

- [ ] **Step 4: Commit**

```bash
git add logs.sh
git commit -m "feat: logs.sh defaults to --tail 100 and passes args through"
```

---

### Task 7: Remove redundant `SUPERVISORCTL` from `clear-ranking.sh`

**Files:**
- Modify: `clear-ranking.sh`

- [ ] **Step 1: Remove the local `SUPERVISORCTL` array**

In `clear-ranking.sh`, delete line 9:

```bash
SUPERVISORCTL=(supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf)
```

The variable is now provided by `docker/_lib.sh` which is sourced on line 6. The rest
of the file is unchanged.

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck -x clear-ranking.sh
```

Expected: no output.

- [ ] **Step 3: Run shellcheck on all scripts together**

```bash
shellcheck -x docker/_lib.sh up.sh down.sh restart.sh status.sh logs.sh clear-ranking.sh
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add clear-ranking.sh
git commit -m "refactor: clear-ranking.sh uses SUPERVISORCTL from _lib.sh"
```
