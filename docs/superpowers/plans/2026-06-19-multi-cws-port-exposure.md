# Multi-CWS Port Exposure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose all CWS shard ports on the Docker host when `CMS_CWS_COUNT > 1` so a reverse proxy can reach each shard.

**Architecture:** Export a derived variable `CMS_CWS_HTTP_PORT_END` in `_lib.sh` at source time, then use Docker Compose's native port-range syntax in `docker-compose.prod.yml` to map the full shard range. Documentation and `.env.example` are updated to explain the port-conflict risk with the defaults.

**Tech Stack:** Bash, Docker Compose v2 port-range syntax, Markdown.

## Global Constraints

- All shell changes must remain compatible with bash (not zsh-only syntax).
- Do not change default port values (`CMS_CWS_HTTP_PORT=8888`, `CMS_AWS_HTTP_PORT=8889`, `CMS_RWS_HTTP_PORT=8890`).
- Do not modify `generate_config.py` — it already generates correct `listen_port` values.
- When `CMS_CWS_COUNT=1`, the behaviour must be identical to before this change (single port exposed).

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `docker/_lib.sh` | Modify lines 17–19 | Export `CMS_CWS_HTTP_PORT_END` |
| `docker/docker-compose.prod.yml` | Modify line 105 | Use port-range syntax for CWS |
| `.env.example` | Modify lines 128–131 | Warn about port conflict |
| `docs/docker-scripts.md` | Insert after line 141 | Add scaling section |

---

### Task 1: Export `CMS_CWS_HTTP_PORT_END` and update compose port mapping

**Files:**
- Modify: `docker/_lib.sh:17-23`
- Modify: `docker/docker-compose.prod.yml:105`

**Interfaces:**
- Produces: env var `CMS_CWS_HTTP_PORT_END` available to all scripts sourcing `_lib.sh` and to `docker compose` variable substitution.

- [ ] **Step 1: Verify the current bug**

  Source `_lib.sh` and check that `CMS_CWS_HTTP_PORT_END` does not exist:
  ```bash
  (source docker/_lib.sh && echo "END=${CMS_CWS_HTTP_PORT_END:-UNSET}")
  ```
  Expected output: `END=UNSET`

- [ ] **Step 2: Add the derived export to `docker/_lib.sh`**

  In `docker/_lib.sh`, after line 19 (the `COMPOSE_CMD=(...)` line) and before line 21
  (the `# Append --profile localdb` comment), insert three lines:

  ```bash
  PROJECT_NAME="$(_env_var CMS_PROJECT_NAME cms-prod)"
  COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.prod.yml"
  COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" -p "$PROJECT_NAME")

  _CWS_BASE_PORT="$(_env_var CMS_CWS_HTTP_PORT 8888)"
  _CWS_COUNT="$(_env_var CMS_CWS_COUNT 1)"
  export CMS_CWS_HTTP_PORT_END=$(( _CWS_BASE_PORT + _CWS_COUNT - 1 ))

  # Append --profile localdb if CMS_USE_LOCALDB=true is persisted in .env
  ```

- [ ] **Step 3: Verify the export with COUNT=1 (no-op case)**

  ```bash
  (source docker/_lib.sh && echo "END=${CMS_CWS_HTTP_PORT_END}")
  ```
  Expected output: `END=8888`

- [ ] **Step 4: Verify the export with COUNT=3**

  ```bash
  (CMS_CWS_COUNT=3 source docker/_lib.sh && echo "END=${CMS_CWS_HTTP_PORT_END}")
  ```
  Expected output: `END=8890`

  If the subshell doesn't pick up the env var via that form, use:
  ```bash
  export CMS_CWS_COUNT=3; source docker/_lib.sh; echo "END=${CMS_CWS_HTTP_PORT_END}"; unset CMS_CWS_COUNT
  ```

- [ ] **Step 5: Update `docker/docker-compose.prod.yml` line 105**

  Replace:
  ```yaml
        - "${CMS_CWS_HTTP_PORT:-8888}:${CMS_CWS_HTTP_PORT:-8888}"
  ```
  With:
  ```yaml
        - "${CMS_CWS_HTTP_PORT:-8888}-${CMS_CWS_HTTP_PORT_END:-8888}:${CMS_CWS_HTTP_PORT:-8888}-${CMS_CWS_HTTP_PORT_END:-8888}"
  ```
  Lines 106 and 107 (`CMS_AWS_HTTP_PORT` and `CMS_LOADER_PORT`) are unchanged.

- [ ] **Step 6: Verify compose resolves correctly with COUNT=1**

  ```bash
  export CMS_CWS_COUNT=1; source docker/_lib.sh
  docker compose -f docker/docker-compose.prod.yml --env-file .env config 2>/dev/null \
    | grep -A5 "ports:" | grep "8888"
  ```
  Expected: a single entry `- 8888:8888` (or equivalent YAML representation).

- [ ] **Step 7: Verify compose resolves correctly with COUNT=3**

  ```bash
  export CMS_CWS_COUNT=3; source docker/_lib.sh
  docker compose -f docker/docker-compose.prod.yml --env-file .env config 2>/dev/null \
    | grep -A5 "ports:" | grep "8888"
  ```
  Expected: a range entry covering `8888-8890`.

- [ ] **Step 8: Commit**

  ```bash
  git add docker/_lib.sh docker/docker-compose.prod.yml
  git commit -m "fix(docker): expose all CWS shard ports via compose port range

  When CMS_CWS_COUNT > 1 each shard runs on CMS_CWS_HTTP_PORT+i but only
  the base port was mapped in docker-compose.prod.yml. Export
  CMS_CWS_HTTP_PORT_END in _lib.sh and use Docker's port-range syntax so
  all shards are reachable from the host reverse proxy."
  ```

---

### Task 2: Update `.env.example` with port-conflict warning

**Files:**
- Modify: `.env.example:128-131`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: updated operator documentation in `.env.example`.

- [ ] **Step 1: Replace the `CMS_CWS_COUNT` comment block**

  Current content (lines 128–131):
  ```
  # Number of Contest Web Server instances to run.
  # Use more than 1 when you have a load balancer (e.g. nginx) in front.
  # Each instance listens on CMS_CWS_HTTP_PORT, CMS_CWS_HTTP_PORT+1, etc.
  CMS_CWS_COUNT=1
  ```

  Replace with:
  ```
  # Number of Contest Web Server instances to run.
  # Use more than 1 when you have a reverse proxy (e.g. nginx) in front.
  # Each shard listens on CMS_CWS_HTTP_PORT+i (shard 0 = base port, shard 1 = base+1, …).
  # All shard ports are exposed on the host automatically.
  #
  # PORT CONFLICT WARNING: with the defaults (CMS_CWS_HTTP_PORT=8888,
  # CMS_AWS_HTTP_PORT=8889), setting CMS_CWS_COUNT=2 puts shard 1 on 8889,
  # which collides with the Admin Web Server. When CMS_CWS_COUNT > 1, set
  # CMS_AWS_HTTP_PORT to CMS_CWS_HTTP_PORT + CMS_CWS_COUNT or higher.
  # Example: CMS_CWS_COUNT=3 → shards on 8888/8889/8890 → set CMS_AWS_HTTP_PORT=8891.
  CMS_CWS_COUNT=1
  ```

- [ ] **Step 2: Verify the file is valid (no broken lines)**

  ```bash
  grep -A12 "^# Number of Contest Web" .env.example
  ```
  Expected: the new comment block followed by `CMS_CWS_COUNT=1`.

- [ ] **Step 3: Commit**

  ```bash
  git add .env.example
  git commit -m "docs(env): warn about CWS/AWS port conflict when CMS_CWS_COUNT > 1"
  ```

---

### Task 3: Add "Scaling Contest Web Server" section to `docs/docker-scripts.md`

**Files:**
- Modify: `docs/docker-scripts.md` — insert after line 141 (end of "Configuring the project name" section, before `## Troubleshooting`).

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: user-facing documentation explaining multi-CWS setup and nginx configuration.

- [ ] **Step 1: Insert the new section**

  After line 141 (`...keep it short and use lowercase letters and hyphens only.`) and before
  line 142 (`## Troubleshooting`), insert:

  ```markdown

  ## Scaling Contest Web Server

  By default, a single `cmsContestWebServer` instance handles all contestant traffic. If you
  need more capacity — typically for contests with hundreds of simultaneous users — you can
  run several instances behind a reverse proxy.

  ### How it works

  Set `CMS_CWS_COUNT` in `.env` to the number of shards you want. Each shard listens on a
  consecutive port starting from `CMS_CWS_HTTP_PORT`:

  | Shard | Port |
  |-------|------|
  | 0 | `CMS_CWS_HTTP_PORT` (e.g. 8888) |
  | 1 | `CMS_CWS_HTTP_PORT + 1` (e.g. 8889) |
  | 2 | `CMS_CWS_HTTP_PORT + 2` (e.g. 8890) |

  All shard ports are automatically exposed on the host when you start with `./up.sh`.

  ### Port conflict warning

  With the defaults (`CMS_CWS_HTTP_PORT=8888`, `CMS_AWS_HTTP_PORT=8889`), setting
  `CMS_CWS_COUNT=2` puts shard 1 on port 8889 — the same port as the Admin Web Server.
  When `CMS_CWS_COUNT > 1`, move `CMS_AWS_HTTP_PORT` above the CWS range:

  ```
  CMS_CWS_COUNT=3
  CMS_CWS_HTTP_PORT=8888   # shards: 8888, 8889, 8890
  CMS_AWS_HTTP_PORT=8891   # must be >= CMS_CWS_HTTP_PORT + CMS_CWS_COUNT
  ```

  ### nginx configuration

  Your reverse proxy must balance incoming contestant requests across all shards. Here is a
  minimal nginx configuration for three shards (`CMS_CWS_COUNT=3`, base port 8888). Adapt
  the port numbers to match your `.env`:

  ```nginx
  upstream cws {
      ip_hash;                        # required: keeps each contestant on the same shard
      server 127.0.0.1:8888;
      server 127.0.0.1:8889;
      server 127.0.0.1:8890;
  }

  server {
      listen 80;
      server_name contest.example.com;

      location / {
          proxy_pass         http://cws;
          proxy_set_header   Host $host;
          proxy_set_header   X-Real-IP $remote_addr;
          proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header   X-Forwarded-Proto $scheme;
      }
  }
  ```

  > **`ip_hash` is required.** Without it, a contestant's requests may land on different
  > shards and their session will be lost. CMS stores session state in-process, not in a
  > shared store.

  Also set `CMS_NUM_PROXIES_USED=1` in `.env` so CMS logs the real contestant IP addresses
  instead of the proxy's address.

  After changing `.env`, run `./restart.sh` to apply.
  ```

- [ ] **Step 2: Verify the section renders correctly**

  ```bash
  grep -n "## Scaling\|## Troubleshooting\|ip_hash\|CMS_NUM_PROXIES" docs/docker-scripts.md
  ```
  Expected: lines for `## Scaling Contest Web Server`, the `ip_hash` lines, and
  `CMS_NUM_PROXIES`, followed by `## Troubleshooting`.

- [ ] **Step 3: Commit**

  ```bash
  git add docs/docker-scripts.md
  git commit -m "docs: add Scaling Contest Web Server section with nginx example"
  ```
