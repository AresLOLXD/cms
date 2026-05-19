# Design: contest.sh Hardening (Sub-project 2)

**Date:** 2026-05-19
**Status:** Approved

## Summary

Harden `contest.sh` by fixing a critical security issue (database password exposed in
process arguments), adding input validation, and replacing non-atomic `.env` writes with
the `_set_env_var` helper introduced in sub-project 1.

**Dependency:** Sub-project 2 must be executed after sub-project 1, which adds
`_set_env_var`, `_psql_password`, and `_psql_url_nopass` to `docker/_lib.sh`.

## Background

`contest.sh` queries the database to list available contests and writes the chosen
contest ID to `.env`. Review findings identified four issues:

| Issue | Severity |
|-------|----------|
| DB password visible in `ps aux` via psql URL argument | Critical |
| No guard when `CMS_DB_URL` is unset | Medium |
| Non-atomic `.env` write (`sed -i` + `echo >>`) | Medium |
| No validation that entered contest ID exists in DB | Low |

## Section 1: DB password in process args

`psql "$PSQL_URL"` passes the full connection URL (including password) as a command
argument, making it visible to all users on the host via `ps aux`.

Two URL utility helpers are added to `docker/_lib.sh`:

```bash
_psql_password()  { echo "$1" | sed 's|.*://[^:]*:\([^@]*\)@.*|\1|'; }
_psql_url_nopass() { echo "$1" | sed 's|\(://[^:]*\):[^@]*@|\1@|'; }
```

`contest.sh` parses the password once and strips it from the URL:

```bash
PGPASSWORD="$(_psql_password "$PSQL_URL")"
PSQL_URL_SAFE="$(_psql_url_nopass "$PSQL_URL")"
```

All psql invocations then use `-e PGPASSWORD="$PGPASSWORD"` with `docker compose exec`
and pass `"$PSQL_URL_SAFE"` instead of `"$PSQL_URL"`. This applies to both call sites:
the `cms` container path and the `db` container fallback.

## Section 2: `CMS_DB_URL` validation

If `CMS_DB_URL` is not set in `.env`, psql fails with a confusing connection error.
An early guard is added after reading the variable:

```bash
DB_URL="$(_env_var CMS_DB_URL "")"
if [[ -z "$DB_URL" ]]; then
  echo "Warning: CMS_DB_URL not set in .env — cannot fetch contests from database."
  echo "You can still enter a contest ID manually."
  CONTESTS=""
fi
```

The script continues to the manual ID prompt rather than aborting — database listing
is best-effort. `PGPASSWORD` and `PSQL_URL_SAFE` are only derived when `DB_URL` is
non-empty.

## Section 3: Atomic `.env` write

The current code has two non-atomic write paths:

- `sed -i "s/^CMS_CONTEST_ID=.*/..."` — replaces existing line
- `echo "CMS_CONTEST_ID=${new_id}" >> "$ENV_FILE"` — appends new line

Both are replaced with a single call to `_set_env_var` (added in sub-project 1),
which writes atomically via a temp file and `mv`:

```bash
_set_env_var "CMS_CONTEST_ID" "$new_id"
```

## Section 4: Contest ID existence validation

After the user enters a syntactically valid integer, the script verifies it exists
in the `contests` table. Validation is skipped when the database is unreachable
(consistent with the best-effort policy for contest listing).

```bash
if [[ -n "$DB_URL" ]]; then
  count=$("${COMPOSE_CMD[@]}" exec -T -e PGPASSWORD="$PGPASSWORD" cms \
      psql "$PSQL_URL_SAFE" -t -A \
      -c "SELECT COUNT(*) FROM contests WHERE id = $new_id;" 2>/dev/null || echo "0")
  if [[ "$count" != "1" ]]; then
    echo "Warning: contest ID $new_id not found in the database."
    if ! ask_yes_no "Write it to .env anyway?" "n"; then
      echo "Aborted."
      exit 0
    fi
  fi
fi
```

Reuses the already-parsed `PGPASSWORD` and `PSQL_URL_SAFE` — no extra URL parsing.

## Files changed

| File | Change |
|------|--------|
| `docker/_lib.sh` | Add `_psql_password()` and `_psql_url_nopass()` helpers |
| `contest.sh` | Guard for empty `CMS_DB_URL`; use password helpers + `-e PGPASSWORD` on both psql call sites; replace `sed -i`/`echo >>` with `_set_env_var`; add contest ID existence check |

## Out of scope

All other script changes are handled in sub-project 1. This spec covers only
`contest.sh` and the two URL helpers added to `_lib.sh`.
