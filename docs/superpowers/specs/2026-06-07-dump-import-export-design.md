# Design: cmsDump Import/Export Scripts

**Date:** 2026-06-07  
**Status:** Approved

## Overview

Add `export.sh` and `import.sh` to the repo root so operators can back up and restore CMS contest data using `cmsDumpExporter` / `cmsDumpImporter` through the production Docker container, following the same interactive style as the existing shell scripts.

## Context

`cmsDumpExporter` and `cmsDumpImporter` must run **inside** the `cms` container because they need direct access to the database and the file cacher. Files are exchanged via a dedicated bind mount that maps `./dumps/` on the host to `/home/cmsuser/cms/dumps` inside the container. This avoids `docker cp` round-trips and keeps dumps easily accessible from the host.

## Changes

### 1. `docker/docker-compose.prod.yml`

Add a bind mount to the `cms` service:

```yaml
volumes:
  - cms-data:/home/cmsuser/cms/lib
  - cms-logs:/home/cmsuser/cms/log
  - cms-cache:/home/cmsuser/cms/cache
  - ./dumps:/home/cmsuser/cms/dumps   # shared dump directory
```

Docker creates `./dumps/` on the host automatically if it does not exist.

### 2. `.dockerignore`

Create (or update) `.dockerignore` at the repo root to exclude the dumps directory from the Docker build context:

```
dumps/
```

This prevents potentially large backup files from being sent to the Docker daemon on every build.

### 3. `.gitignore`

Add `dumps/` so backup archives are never committed accidentally.

### 4. `export.sh`

Interactive script. Flow:

1. **Select contests** — query available contests via `docker compose exec cms python3 -c "..."` (or equivalent CMS CLI). Display a numbered list. User chooses: all, or specific IDs (space-separated).
2. **Output filename** — prompt with default `dumps/export-YYYY-MM-DD.zip`.
3. **Exclusion options** — three `ask_yes_no` prompts (default `n` each):
   - Skip submissions? (`-S`)
   - Skip users? (`-X`)
   - Skip generated files? (`-G`)
4. **Run** — execute `docker compose exec cms cmsDumpExporter [flags] -c <ids> dumps/<name>.zip`. Stream stdout/stderr live so CMS's own progress messages are visible. A background spinner shows elapsed time so operators know the process is alive during quiet stretches.
5. **Confirm** — on success, print the output filename and its size on the host.

### 5. `import.sh`

Interactive script. Flow:

1. **List available dumps** — scan `./dumps/` and display a numbered list with filename and size. Exit with a clear message if the directory is empty.
2. **Select file** — user picks by number.
3. **Drop DB?** — `ask_yes_no` with a prominent warning (printed in red using ANSI codes), default `n`. Passes `-d` to the importer if confirmed.
4. **Exclusion options** — three `ask_yes_no` prompts (default `n` each):
   - Skip submissions? (`-S`)
   - Skip users? (`-X`)
   - Skip generated files? (`-G`)
5. **Final confirmation** — summarize the chosen file and flags, ask "Proceed?" (default `n`).
6. **Run** — execute `docker compose exec cms cmsDumpImporter [flags] dumps/<file>`. Stream stdout/stderr live with a background elapsed-time spinner, same as export.
7. **Result** — print success or failure message.

### 6. Progress / spinner

Both scripts use a shared helper (added to `docker/_lib.sh`) that:

- Runs the given command with output streamed directly to the terminal.
- In a background subshell, prints an elapsed-time counter (`[12s]`) that overwrites itself on the same line using `\r`, so it does not interfere with the streamed output.
- Cleans up the background subshell and prints a final elapsed time on completion.

Because `docker compose exec` streams CMS log lines as they arrive, operators see real progress. The spinner supplements this during quiet gaps (e.g., while large files are being written).

### 7. `docs/docker-scripts.md`

Add a section documenting `export.sh` and `import.sh`: purpose, typical usage, and what each menu option does.

## Non-goals

- No scheduled / automated backups (cron, etc.).
- No remote upload of dump files.
- No encryption of dumps.
- No rotation / cleanup of old dump files.
