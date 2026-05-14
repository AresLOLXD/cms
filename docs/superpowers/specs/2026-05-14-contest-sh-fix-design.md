# Design: Fix `contest.sh` DB listing + README helper-script docs

Date: 2026-05-14

## Problem

Running `./contest.sh` always prints "No contests found in the database" even
when contests exist. Two root causes:

1. **Wrong connection strategy.** The script does
   `docker compose exec -T db psql …`, which only works when the `db` Docker
   service is running (i.e. `--profile localdb`). Deployments that use an
   external PostgreSQL instance (e.g. `CMS_DB_URL` pointing to `172.19.0.1`)
   have no `db` container, so the exec fails.

2. **Silent failure.** The command is wrapped in `2>/dev/null || CONTESTS=""`,
   so any failure — wrong container name, auth error, network error — is
   silently swallowed and the output is treated as "no rows".

3. **Mismatched credentials (secondary).** The script reads `POSTGRES_USER`
   (default `cms`) but the actual DB user in `CMS_DB_URL` is `cmsuser`.

## Solution

Replace the single docker-exec strategy with a two-step fallback:

1. **Try `psql` directly** using `CMS_DB_URL` (strip the SQLAlchemy driver
   suffix `+psycopg2` to produce a standard `postgresql://` URL). This works
   for external DBs and for `localhost`/`127.x.x.x` hosts.

2. **Fall back to `docker compose exec -T db psql`** if the direct call fails.
   This covers the `--profile localdb` case where the DB hostname is `db` and
   only resolves inside the Docker network.

3. If both fail, `CONTESTS=""` — same silent fallback as before.

The `POSTGRES_USER` / `POSTGRES_DB` variables are only used in the docker-exec
fallback path, where they are still correct (the `db` container's internal
superuser is `cms`).

## Changes

### `contest.sh`

Replace lines 22–31 (the `ADMIN_PORT` + docker-exec block):

```bash
# Before
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

```bash
# After
ADMIN_PORT="$(_env_var CMS_AWS_HTTP_PORT 8889)"
DB_URL="$(_env_var CMS_DB_URL "")"
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

Flags used: `-t` suppresses column headers, `-A` removes alignment padding —
produces clean `id - name` lines.

### `README.md`

Add a **Helper scripts** section after "Common operations". Documents all
scripts in the repo root with a table:

| Script | What it does |
|--------|-------------|
| `./up.sh` | Start services (asks whether to use local DB and whether to rebuild) |
| `./down.sh` | Stop and remove containers |
| `./restart.sh` | `down` + `up` |
| `./logs.sh` | Follow live logs |
| `./status.sh` | Show container status |
| `./contest.sh` | Switch the active contest (`CMS_CONTEST_ID` in `.env`) |

## Testing

- External DB (current setup): direct `psql` succeeds, lists contests.
- `--profile localdb`: direct `psql` fails (host `db` unresolvable), falls
  back to docker exec, lists contests.
- Both DB unreachable: both fail, `CONTESTS=""`, script shows fallback message
  and still allows manual ID entry.
