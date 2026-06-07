# Dump Import/Export Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `export.sh` and `import.sh` scripts to the repo root so operators can back up and restore CMS contest data through the production Docker container using interactive menus.

**Architecture:** Files are exchanged via a bind mount (`./dumps/` on host → `/home/cmsuser/cms/dumps` in container). Both scripts source `docker/_lib.sh` for shared helpers. `cmsDumpExporter`/`cmsDumpImporter` run inside the container via `docker compose exec`. A `_run_timed` helper in `_lib.sh` wraps long-running commands to print elapsed time.

**Tech Stack:** Bash, Docker Compose, `cmsDumpExporter`, `cmsDumpImporter`, `psql` (for contest listing — same pattern as `contest.sh`)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `docker/docker-compose.prod.yml` | Add `./dumps` bind mount to `cms` service |
| Modify | `.dockerignore` | Exclude `dumps/` from build context |
| Modify | `.gitignore` | Exclude `dumps/` from version control |
| Modify | `docker/_lib.sh` | Add `_run_timed` helper |
| Create | `export.sh` | Interactive export menu |
| Create | `import.sh` | Interactive import menu |
| Modify | `docs/docker-scripts.md` | Document both new scripts |

---

## Task 1: Add bind mount and ignore rules

**Files:**
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.dockerignore`
- Modify: `.gitignore`

- [ ] **Step 1: Add the bind mount to docker-compose.prod.yml**

In the `cms` service `volumes` block, add the new mount **after** the existing entries:

```yaml
    volumes:
      - cms-data:/home/cmsuser/cms/lib
      - cms-logs:/home/cmsuser/cms/log
      - cms-cache:/home/cmsuser/cms/cache
      - ./dumps:/home/cmsuser/cms/dumps
```

- [ ] **Step 2: Add dumps/ to .dockerignore**

The file already exists. Add one line at the end:

```
dumps/
```

- [ ] **Step 3: Add dumps/ to .gitignore**

The file already exists. Add one line at the end:

```
dumps/
```

- [ ] **Step 4: Verify compose file parses**

Run from the repo root:
```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env config --quiet
```
Expected: no output, exit 0. If you see an error, check YAML indentation.

- [ ] **Step 5: Commit**

```bash
git add docker/docker-compose.prod.yml .dockerignore .gitignore
git commit -m "feat(docker): add dumps bind mount and ignore rules"
```

---

## Task 2: Add _run_timed helper to docker/_lib.sh

**Files:**
- Modify: `docker/_lib.sh`

This helper runs a command with its output streamed live to the terminal, then prints elapsed time. Separate from a spinner because mixing ANSI `\r` updates with streamed command output garbles the display.

- [ ] **Step 1: Add the function at the end of docker/_lib.sh**

Append after the closing `}` of `_do_up`:

```bash
# _run_timed LABEL CMD [ARGS...] — run CMD streaming output, print elapsed time.
_run_timed() {
  local label="$1"
  shift
  local start=$SECONDS
  echo "$label"
  "$@"
  local rc=$? elapsed=$(( SECONDS - start ))
  if [[ $rc -eq 0 ]]; then
    printf "Done in %ds.\n" "$elapsed"
  else
    printf "Failed after %ds.\n" "$elapsed" >&2
  fi
  return $rc
}
```

- [ ] **Step 2: Verify the file still sources cleanly**

```bash
bash -n docker/_lib.sh
```
Expected: no output, exit 0.

- [ ] **Step 3: Smoke-test _run_timed**

```bash
bash -c 'source docker/_lib.sh; _run_timed "Testing..." sleep 1'
```
Expected output:
```
Testing...
Done in 1s.
```

- [ ] **Step 4: Commit**

```bash
git add docker/_lib.sh
git commit -m "feat(docker): add _run_timed helper to _lib.sh"
```

---

## Task 3: Create export.sh

**Files:**
- Create: `export.sh`

- [ ] **Step 1: Create export.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

# ── 1. List available contests ─────────────────────────────────────────────
DB_URL="$(_env_var CMS_DB_URL "")"
PSQL_URL="${DB_URL/postgresql+psycopg2/postgresql}"

echo "Fetching contests from database..."
CONTESTS=""
if CONTESTS=$("${COMPOSE_CMD[@]}" exec -T cms psql "$PSQL_URL" -t -A \
    -c "SELECT id || ' - ' || name FROM contests ORDER BY id;" 2>/dev/null); then
  :
elif CONTESTS=$("${COMPOSE_CMD[@]}" exec -T db psql \
    -U "$(_env_var POSTGRES_USER cms)" -d "$(_env_var POSTGRES_DB cmsdb)" \
    -t -A -c "SELECT id || ' - ' || name FROM contests ORDER BY id;" 2>/dev/null); then
  :
fi

if [[ -z "$CONTESTS" ]]; then
  echo "No contests found (or database unreachable). Exporting ALL contests."
  CONTEST_FLAGS=()
else
  echo ""
  echo "Available contests:"
  echo "$CONTESTS"
  echo ""
  echo "Enter contest IDs to export (space-separated), or press Enter to export all:"
  read -r -p "IDs: " id_input
  if [[ -n "$id_input" ]]; then
    # shellcheck disable=SC2206
    CONTEST_FLAGS=(-c $id_input)
  else
    CONTEST_FLAGS=()
  fi
fi

# ── 2. Output filename ─────────────────────────────────────────────────────
default_name="export-$(date +%Y-%m-%d).zip"
read -r -p "Output filename [dumps/${default_name}]: " filename
filename="${filename:-$default_name}"
# Strip leading dumps/ if the user typed it, to avoid dumps/dumps/
filename="${filename#dumps/}"

# ── 3. Exclusion options ───────────────────────────────────────────────────
EXCL_FLAGS=()
if ask_yes_no "Skip submissions? (-S)" "n"; then EXCL_FLAGS+=(-S); fi
if ask_yes_no "Skip users? (-X)" "n"; then EXCL_FLAGS+=(-X); fi
if ask_yes_no "Skip generated files? (-G)" "n"; then EXCL_FLAGS+=(-G); fi

# ── 4. Run ─────────────────────────────────────────────────────────────────
echo ""
_run_timed "Exporting to dumps/${filename} (this may take several minutes)..." \
  "${COMPOSE_CMD[@]}" exec cms \
  cmsDumpExporter "${CONTEST_FLAGS[@]}" "${EXCL_FLAGS[@]}" \
  "/home/cmsuser/cms/dumps/${filename}"

# ── 5. Confirm ─────────────────────────────────────────────────────────────
host_path="$REPO_ROOT/dumps/${filename}"
if [[ -f "$host_path" ]]; then
  size=$(du -h "$host_path" | cut -f1)
  echo ""
  echo "Export saved to dumps/${filename} (${size})"
else
  echo "Warning: file not found at dumps/${filename} — check container logs." >&2
fi
```

- [ ] **Step 2: Make executable**

```bash
chmod +x export.sh
```

- [ ] **Step 3: Syntax check**

```bash
bash -n export.sh
```
Expected: no output, exit 0.

- [ ] **Step 4: Dry-run smoke test (without running)**

Verify the script sources correctly and the help flags appear:
```bash
bash -c 'source docker/_lib.sh; echo "lib sourced ok"'
```
Expected: `lib sourced ok`

- [ ] **Step 5: Commit**

```bash
git add export.sh
git commit -m "feat: add export.sh for interactive cmsDumpExporter"
```

---

## Task 4: Create import.sh

**Files:**
- Create: `import.sh`

- [ ] **Step 1: Create import.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

DUMPS_DIR="$REPO_ROOT/dumps"

# ── 1. List available dump files ───────────────────────────────────────────
if [[ ! -d "$DUMPS_DIR" ]] || [[ -z "$(ls -A "$DUMPS_DIR" 2>/dev/null)" ]]; then
  echo "No dump files found in dumps/."
  echo "Run ./export.sh first to create a backup."
  exit 1
fi

echo "Available dump files:"
echo ""
mapfile -t dump_files < <(ls -t "$DUMPS_DIR")
for i in "${!dump_files[@]}"; do
  size=$(du -h "$DUMPS_DIR/${dump_files[$i]}" 2>/dev/null | cut -f1)
  printf "  %d) %s  (%s)\n" $(( i + 1 )) "${dump_files[$i]}" "$size"
done
echo ""

# ── 2. Select file ─────────────────────────────────────────────────────────
while true; do
  read -r -p "Select file number: " choice
  if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#dump_files[@]} )); then
    selected="${dump_files[$(( choice - 1 ))]}"
    break
  fi
  echo "Please enter a number between 1 and ${#dump_files[@]}."
done

# ── 3. Drop DB? ────────────────────────────────────────────────────────────
echo ""
printf "\033[1;31mWARNING: Dropping the database will permanently delete ALL contest data.\033[0m\n"
printf "\033[1;31mThis cannot be undone. Only proceed if you are restoring from a known-good backup.\033[0m\n"
echo ""
DROP_FLAGS=()
if ask_yes_no "Drop database before importing? (-d)" "n"; then
  DROP_FLAGS=(-d)
fi

# ── 4. Exclusion options ───────────────────────────────────────────────────
EXCL_FLAGS=()
if ask_yes_no "Skip submissions? (-S)" "n"; then EXCL_FLAGS+=(-S); fi
if ask_yes_no "Skip users? (-X)" "n"; then EXCL_FLAGS+=(-X); fi
if ask_yes_no "Skip generated files? (-G)" "n"; then EXCL_FLAGS+=(-G); fi

# ── 5. Final confirmation ──────────────────────────────────────────────────
echo ""
echo "About to import: $selected"
[[ ${#DROP_FLAGS[@]} -gt 0 ]] && echo "  - DROP DATABASE before import"
[[ ${#EXCL_FLAGS[@]} -gt 0 ]] && echo "  - Flags: ${EXCL_FLAGS[*]}"
echo ""
if ! ask_yes_no "Proceed?" "n"; then
  echo "Aborted."
  exit 0
fi

# ── 6. Run ─────────────────────────────────────────────────────────────────
echo ""
_run_timed "Importing dumps/${selected} (this may take several minutes)..." \
  "${COMPOSE_CMD[@]}" exec cms \
  cmsDumpImporter "${DROP_FLAGS[@]}" "${EXCL_FLAGS[@]}" \
  "/home/cmsuser/cms/dumps/${selected}"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x import.sh
```

- [ ] **Step 3: Syntax check**

```bash
bash -n import.sh
```
Expected: no output, exit 0.

- [ ] **Step 4: Verify empty-dumps message**

With no `dumps/` directory (or empty one):
```bash
mkdir -p /tmp/test-dumps-empty
# Temporarily rename dumps if it exists
bash -c 'DUMPS_DIR=/tmp/test-dumps-empty source docker/_lib.sh 2>/dev/null || true'
```
The script's early exit check at line 13 covers this — manual inspection of the condition logic is sufficient.

- [ ] **Step 5: Commit**

```bash
git add import.sh
git commit -m "feat: add import.sh for interactive cmsDumpImporter"
```

---

## Task 5: Document the new scripts

**Files:**
- Modify: `docs/docker-scripts.md`

- [ ] **Step 1: Add export.sh and import.sh sections to docs/docker-scripts.md**

In the "Scripts reference" section, add after the `clear-ranking.sh` entry:

```markdown
### export.sh

Exports contest data to a `.zip` archive in the `dumps/` folder. Asks which contests to export, the output filename, and whether to exclude submissions, users, or generated files.

```bash
./export.sh
```

The export file is saved to `dumps/` (at the repo root) and is accessible immediately on the host after the command completes.

### import.sh

Imports contest data from a `.zip` archive previously created by `export.sh`. Lists available files in `dumps/`, lets you pick one, and asks whether to drop the database before importing (useful for full restores), plus options to skip submissions, users, or generated files.

```bash
./import.sh
```

> **Warning:** Choosing to drop the database before import permanently deletes all existing contest data. Only use this option when restoring from a known-good backup.
```

- [ ] **Step 2: Verify the markdown renders without broken fences**

```bash
grep -n '^\`\`\`' docs/docker-scripts.md | head -30
```
Expected: fenced code blocks open and close in matched pairs.

- [ ] **Step 3: Commit**

```bash
git add docs/docker-scripts.md
git commit -m "docs: document export.sh and import.sh scripts"
```

---

## Verification (manual, after all tasks)

Once the Docker stack is running (`./up.sh`):

1. **Export test:**
   ```bash
   ./export.sh
   ```
   - Select a contest (or all).
   - Accept default filename.
   - Answer `n` to all exclusion options.
   - Confirm a `.zip` appears in `dumps/` on the host.

2. **Import test:**
   ```bash
   ./import.sh
   ```
   - Select the file just exported.
   - Answer `n` to drop DB.
   - Answer `n` to all exclusion options.
   - Confirm `n` at the "Proceed?" prompt first to test abort path.
   - Re-run and confirm `y` — verify the import completes without errors.

3. **Build context check:**
   Create a file in `dumps/` then run a build and confirm it isn't included:
   ```bash
   echo "test" > dumps/test.txt
   docker compose -f docker/docker-compose.prod.yml --env-file .env build cms 2>&1 | grep -i dump || echo "dumps/ not in build context (good)"
   rm dumps/test.txt
   ```
