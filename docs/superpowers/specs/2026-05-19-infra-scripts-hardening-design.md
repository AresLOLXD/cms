# Design: Infrastructure Scripts Hardening (Sub-project 1)

**Date:** 2026-05-19
**Status:** Approved

## Summary

Harden the six infrastructure management scripts (`up.sh`, `down.sh`, `restart.sh`,
`status.sh`, `logs.sh`, `clear-ranking.sh`) and their shared library (`docker/_lib.sh`)
based on findings from a multi-expert review. `contest.sh` is handled separately in
sub-project 2.

## Background

The scripts share `docker/_lib.sh` which provides `COMPOSE_CMD`, `ask_yes_no`, and
`_env_var`. Review findings identified six categories of issues:

- Missing localdb profile persistence across scripts
- `restart.sh` spawning a subprocess for `up.sh` (re-asks questions, can't share state)
- No confirmation guard before destructive `down` operation
- `status.sh` showing only container-level state, not service-level
- `logs.sh` dumping full log history by default
- `SUPERVISORCTL` array defined locally in `clear-ranking.sh` instead of shared

## Section 1: `docker/_lib.sh` changes

### `CMS_USE_LOCALDB` persistence

When `up.sh` asks "Use local PostgreSQL container?" and the user answers yes, that choice
is written to `.env` as `CMS_USE_LOCALDB=true`. On every subsequent `source _lib.sh`,
if `CMS_USE_LOCALDB=true` is present in `.env`, `--profile localdb` is appended to
`COMPOSE_CMD`. This makes `down.sh`, `status.sh`, `logs.sh`, and `restart.sh` all
see the db container automatically.

When the user answers no, `CMS_USE_LOCALDB` is written as `false` (or removed if
already absent) so the choice is explicit.

### `_set_env_var(key, value)` helper

Writes or updates a `KEY=value` line in `.env` at the repo root. Uses `grep -q` to
detect whether the key already exists, then either replaces the line with `sed -i` or
appends it. Operates atomically: writes to `.env.tmp` and moves it over `.env`.

### `_do_up()` function

Extracts the interactive up logic so both `up.sh` and `restart.sh` can call it without
spawning a subprocess:

1. Docker pre-flight: verify Docker daemon is reachable (`docker info 2>/dev/null`)
   before attempting any compose commands. On failure: print error and exit 1.
2. Ask "Use local PostgreSQL container? [y/N]" — persist answer via `_set_env_var`.
3. Re-source the COMPOSE_CMD (to pick up the newly-written localdb flag).
4. Run `"${COMPOSE_CMD[@]}" up -d`.
5. Wait for the `cms` container to be healthy (poll healthcheck, up to 90 s).

### `ask_yes_no` EOF fix

Add `|| break` to the `read` call inside `ask_yes_no` so non-interactive invocations
(piped input, CI) don't loop forever on EOF.

### `SUPERVISORCTL` array

Move the supervisorctl invocation array here so all scripts share the same path:

```bash
SUPERVISORCTL=(supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf)
```

## Section 2: `up.sh` and `down.sh`

**`up.sh`** becomes a thin wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/docker/_lib.sh"
_do_up
```

The Docker pre-flight check and all interactive logic live in `_do_up()`.

**`down.sh`** adds a confirmation guard before tearing down services:

```bash
if ! ask_yes_no "Stop all services? Contestants will be disconnected." "n"; then
  echo "Aborted."
  exit 0
fi
```

No other changes to `down.sh`.

## Section 3: `restart.sh`

Updated flow:

1. Source `_lib.sh`
2. `ask_yes_no "Restart all services?" "n"` — abort if no
3. `"${COMPOSE_CMD[@]}" down`
4. `_do_up()` — handles localdb, pre-flight, compose up, healthy wait
5. `"${COMPOSE_CMD[@]}" ps` — sanity check showing running containers

The `bash up.sh` subprocess call is removed entirely.

## Section 4: `status.sh` and `logs.sh`

**`status.sh`** adds three best-effort sections before the existing `compose ps` output:

1. **Project name** — `echo "Project: $(_env_var CMS_PROJECT_NAME cms)"`
2. **Supervisord status** — `"${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" status`
   Shows which CMS services are running inside the container.
3. **Active contest ID** — reads from `cms.conf` inside the container via
   `grep -o '"contest_id":[0-9]*' /home/cmsuser/cms/etc/cms.conf | head -1`

All `exec -T` calls are wrapped in `|| true` so `status.sh` degrades gracefully when
the container is not running.

**`logs.sh`** prepends `--tail 100` when no `--tail` flag is present in `"$@"`:

```bash
has_tail=false
for arg in "$@"; do
  [[ "$arg" == --tail* ]] && has_tail=true && break
done
TAIL_ARGS=()
$has_tail || TAIL_ARGS=(--tail 100)
"${COMPOSE_CMD[@]}" logs "${TAIL_ARGS[@]}" "$@"
```

## Section 5: `clear-ranking.sh`

Remove the local `SUPERVISORCTL` array definition — it is now sourced from `_lib.sh`.
No other changes.

## Files changed

| File | Change |
|------|--------|
| `docker/_lib.sh` | Add `CMS_USE_LOCALDB` → `COMPOSE_CMD`; add `_set_env_var`; add `_do_up`; fix `ask_yes_no` EOF; add `SUPERVISORCTL` array |
| `up.sh` | Replace all inline logic with `_do_up` call |
| `down.sh` | Add confirmation guard before `compose down` |
| `restart.sh` | Add confirmation guard; replace `bash up.sh` with `_do_up`; add `compose ps` after up |
| `status.sh` | Add project name, supervisord status, active contest ID |
| `logs.sh` | Add `--tail 100` default |
| `clear-ranking.sh` | Remove local `SUPERVISORCTL` array |

## Out of scope

`contest.sh` hardening (DB password exposure, atomic `.env` write, contest ID validation)
is handled in sub-project 2.
