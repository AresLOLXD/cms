# contest.sh Fix + README Helper Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `contest.sh` to correctly list contests from any DB topology (external or Docker-managed), and document all root helper scripts in README.md.

**Architecture:** Replace the single `docker compose exec -T db psql` call with a two-step fallback: try `psql` directly via `CMS_DB_URL`, fall back to docker exec for `--profile localdb` setups. Add a "Helper scripts" section to README.md.

**Tech Stack:** Bash, PostgreSQL (`psql` CLI), Docker Compose, Markdown.

---

### Task 1: Fix DB listing in `contest.sh`

**Files:**
- Modify: `contest.sh:22-31`

There are no automated tests for shell scripts in this repo — verify manually by dry-running the command substitution logic.

- [ ] **Step 1: Open `contest.sh` and locate the block to replace**

  Lines 22–31 currently read:

  ```bash
  ADMIN_PORT="$(_env_var CMS_AWS_HTTP_PORT 8889)"
  DB_USER="$(_env_var POSTGRES_USER cms)"
  DB_NAME="$(_env_var POSTGRES_DB cmsdb)"

  echo "Fetching contests from database..."
  CONTESTS=$(
    "${COMPOSE_CMD[@]}" exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
      -c "SELECT id, name FROM contests ORDER BY id;" \
      2>/dev/null
  ) || CONTESTS=""
  ```

- [ ] **Step 2: Replace with the two-step fallback**

  Replace those exact lines with:

  ```bash
  ADMIN_PORT="$(_env_var CMS_AWS_HTTP_PORT 8889)"
  DB_URL="$(_env_var CMS_DB_URL "")"
  # psql accepts postgresql:// but not the SQLAlchemy +psycopg2 driver suffix
  PSQL_URL="${DB_URL/postgresql+psycopg2/postgresql}"

  echo "Fetching contests from database..."
  if CONTESTS=$(psql "$PSQL_URL" -t -A \
      -c "SELECT id || ' - ' || name FROM contests ORDER BY id;" 2>/dev/null); then
    :
  elif CONTESTS=$("${COMPOSE_CMD[@]}" exec -T db psql \
      -U "$(_env_var POSTGRES_USER cms)" -d "$(_env_var POSTGRES_DB cmsdb)" \
      -t -A -c "SELECT id || ' - ' || name FROM contests ORDER BY id;" 2>/dev/null); then
    :
  else
    CONTESTS=""
  fi
  ```

  Flags: `-t` suppresses column headers; `-A` removes alignment padding — produces clean `24 - My Contest` lines.

- [ ] **Step 3: Verify the script is syntactically valid**

  Run:
  ```bash
  bash -n contest.sh
  ```
  Expected: no output (exit 0).

- [ ] **Step 4: Smoke-test the listing against your live DB**

  Run:
  ```bash
  ./contest.sh
  ```
  Expected output similar to:
  ```
  Current contest ID: 24

  Fetching contests from database...
  Available contests:
  24 - <your contest name>

  Enter new contest ID:
  ```
  Press Ctrl-C to abort without changing anything.

- [ ] **Step 5: Commit**

  ```bash
  git add contest.sh
  git commit -m "fix: use psql direct + docker exec fallback in contest.sh

  The previous docker compose exec -T db approach only worked with
  --profile localdb. Deployments using an external DB had no db
  container, causing the listing to silently fail every time.

  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
  ```

---

### Task 2: Document helper scripts in README.md

**Files:**
- Modify: `README.md` (after line 204, before the Testimonials section)

- [ ] **Step 1: Locate the insertion point**

  In `README.md`, find the `---` separator at line 205 (after the Troubleshooting section, before "Testimonials"). The new section goes between those two.

- [ ] **Step 2: Insert the Helper scripts section**

  Add the following block between the `---` on line 205 and the `Testimonials` heading:

  ```markdown
  ### Helper scripts

  The repo root contains convenience wrappers around the `docker compose` commands.
  Run them from the repo root — they read `.env` automatically.

  | Script | What it does |
  |--------|-------------|
  | `./up.sh` | Start services. Asks whether to use a local Docker-managed database (`--profile localdb`) and whether to rebuild the image. |
  | `./down.sh` | Stop and remove all containers. |
  | `./restart.sh` | `down` followed by `up` (prompts again for local DB / rebuild). |
  | `./logs.sh` | Follow live logs for all services. |
  | `./status.sh` | Show the running status of all containers. |
  | `./contest.sh` | Switch the active contest: lists available contests from the database, prompts for a new ID, and updates `CMS_CONTEST_ID` in `.env`. Optionally restarts services to apply the change. |

  ```

- [ ] **Step 3: Verify the README renders correctly**

  Run:
  ```bash
  grep -n "Helper scripts" README.md
  ```
  Expected: one match on the line you inserted.

  Optionally preview with a Markdown renderer, or just scan the raw file around the insertion point to confirm the table looks right.

- [ ] **Step 4: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add Helper scripts section to README

  Documents all root-level convenience wrappers (up, down, restart,
  logs, status, contest) so operators know the scripts exist and
  what each one does.

  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
  ```
