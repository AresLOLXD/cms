#!/usr/bin/env bash
set -euo pipefail

# Proves that a backup written by backup.sh restores: the checksums, a restore
# into a throwaway PostgreSQL container, then the counts and the large objects.
# Needs only Docker: no .env, no CMS.
# Usage: ./verify-backup.sh <backup dir>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_backup_lib.sh
source "$SCRIPT_DIR/docker/_backup_lib.sh"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

failures=0
# fail MESSAGE — name a failed check and go on, so one run reports them all.
fail() {
  echo "FAILED: $*" >&2
  failures=$((failures + 1))
}

# ── 1. The directory and its checksums ──────────────────────────────────────
(( $# == 1 )) || { echo "Usage: ./verify-backup.sh <backup dir>" >&2; exit 1; }
# backups/ is root's with mode 700: without sudo, -d cannot see inside it.
[[ -d "$1" ]] || die "$1 is not a directory you can open: check the path, or run it with sudo"
dir="$(cd -- "$1" && pwd -P)" || die "cannot enter $1"
[[ "$dir" != *.partial ]] || die "$dir is a .partial directory: backup.sh did not finish it"
# The files are root-owned with no access for others, and sha256sum runs here.
[[ -r "$dir/MANIFEST" && -r "$dir/SHA256SUMS" ]] \
  || die "cannot read MANIFEST and SHA256SUMS in $dir: is it a backup of backup.sh?" \
         "Run it with sudo, or as the owner of the files"
unreadable="$(find "$dir" -type f ! -readable -print -quit)"
[[ -z "$unreadable" ]] \
  || die "cannot read $unreadable: run it with sudo, or as the owner of the files"

echo "Checking the checksums..."
(cd "$dir" && sha256sum --quiet --strict -c SHA256SUMS) >&2 \
  || die "the checksums do not match: the files listed above are corrupted or missing"

# manifest_value KEY — the first value of KEY in MANIFEST; unknown keys are ignored.
manifest_value() {
  awk -F'\t' -v key="$1" '$1 == key { print $2; exit }' "$dir/MANIFEST"
}
dump_version="$(manifest_value pg_dump_version)"
[[ "$dump_version" =~ ^[0-9]+ ]] || die "MANIFEST has no pg_dump_version"
# The dump's major (17 or newer) restores without the transaction_timeout error.
major="$(pick_pg_major "$(( 10#${BASH_REMATCH[0]} * 10000 ))")"

# ── 2. The throwaway server ─────────────────────────────────────────────────
container="cms-verify-$$"

cleanup() {
  local status=$?
  # A second Ctrl+C must not stop the cleanup half way, and a dead terminal
  # must not get any output. -v removes the anonymous volume of the image.
  trap '' INT TERM HUP
  # A docker run stopped while the daemon creates its container ends at once,
  # and the container appears just after (up to 0.6 s with Docker 29), in the
  # Created state: give it time to appear.
  if (( status > 128 )); then sleep 2; fi
  docker rm -f -v "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Starting a throwaway postgres:$major container..."
# The image creates the anonymous volume. No network: everything runs through
# docker exec. The password is random per run and goes in by name only.
password="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
POSTGRES_PASSWORD="$password" docker run -d --name "$container" --network none \
  -v "$dir:/backup:ro,z" -e POSTGRES_PASSWORD "postgres:$major" -c fsync=off >/dev/null
unset password

# The image's init runs a temporary server that only listens on its socket and
# is then stopped, so wait for the final one, which answers on TCP.
ready=""
for (( tries = 0; tries < 120; tries++ )); do
  if docker exec "$container" pg_isready -q -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ -z "$ready" ]]; then
  docker logs --tail 20 "$container" >&2 || true
  die "the throwaway database did not start in 120 s"
fi

# sql QUERY — run QUERY in the throwaway database and print the rows.
sql() {
  docker exec "$container" psql -X -q -At -F $'\t' -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "$1" || die "the query failed: $1"
}

# ── 3. Restore ──────────────────────────────────────────────────────────────
workers="$(nproc)"
echo "Restoring with $workers workers..."
# root reads the backup whoever owns it; the image's postgres user could not.
# shortcut: it restores into the image's default UTF8 database, not one with
# the encoding and locale of the MANIFEST; add a CREATE DATABASE if a
# non-UTF8 database ever matters.
# Unlike a foreground command, wait lets the INT and TERM traps run at once.
docker exec -u root "$container" pg_restore -U postgres -d postgres -j "$workers" \
  --no-owner --no-acl --exit-on-error /backup &
wait "$!" || die "pg_restore failed"

# ── 4. Checks ───────────────────────────────────────────────────────────────
echo "Checking the restored database..."
sql "CREATE EXTENSION IF NOT EXISTS pgcrypto" >/dev/null

# One row per table of public, then the large objects, like the MANIFEST.
counts_sql=$(cat <<'SQL'
SELECT tablename, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', tablename), false, true, '')))[1]::text
  FROM pg_tables WHERE schemaname = 'public'
UNION ALL SELECT '(large objects)', count(*)::text FROM pg_largeobject_metadata
SQL
)
expected=$(awk -F'\t' '$1 == "table" { print $2 "\t" $3 }
                       $1 == "large_objects" { print "(large objects)\t" $2 }' "$dir/MANIFEST")
actual=$(sql "$counts_sql")
if ! differences=$(manifest_compare "$expected" "$actual"); then
  fail "the counts differ from the MANIFEST (MANIFEST vs restored):"$'\n'"$differences"
fi

missing=$(sql "SELECT count(*) FROM fsobjects f LEFT JOIN pg_largeobject_metadata m ON m.oid = f.loid WHERE m.oid IS NULL")
(( missing == 0 )) || fail "fsobjects.loid: $missing rows point to a large object that does not exist"

# A missing object is counted above; joining keeps lo_get away from it.
wrong=$(sql "SELECT count(*) FROM fsobjects f JOIN pg_largeobject_metadata m ON m.oid = f.loid WHERE encode(digest(lo_get(f.loid), 'sha1'), 'hex') <> f.digest")
(( wrong == 0 )) || fail "SHA1 of the large objects: $wrong rows whose content does not match fsobjects.digest"

orphans=$(sql "SELECT count(*) FROM pg_largeobject_metadata m WHERE NOT EXISTS (SELECT 1 FROM fsobjects f WHERE f.loid = m.oid)")
(( orphans == 0 )) || echo "Info: $orphans orphan large objects (no fsobjects row uses them); not an error"

# ── 5. Verdict ──────────────────────────────────────────────────────────────
(( failures == 0 )) || die "$failures check(s) failed: do not trust this backup"
echo "OK: $dir restores; the checksums, the counts and the large objects match."
