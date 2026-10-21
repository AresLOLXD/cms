#!/usr/bin/env bash
set -euo pipefail

# Consistent full backup of the CMS database with pg_dump, into backups/.
# Usage: sudo ./backup.sh [label] [-j N]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"
# shellcheck source=docker/_backup_lib.sh
source "$SCRIPT_DIR/docker/_backup_lib.sh"

umask 077

usage() {
  echo "Usage: sudo ./backup.sh [label] [-j N]" >&2
  exit 1
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

label="" workers=4
while (( $# )); do
  case "$1" in
    -j) (( $# >= 2 )) || usage; workers="$2"; shift 2 ;;
    -*) usage ;;
    *) [[ -z "$label" ]] || usage; label="$1"; shift ;;
  esac
done
[[ "$workers" =~ ^[1-9][0-9]*$ ]] || die "-j needs a number of workers (got: $workers)"
name="$(backup_name "$label")"
# The dump is written by root in the container: only root can remove it.
[[ "$(id -u)" == 0 ]] || die "run it as root: sudo ./backup.sh [label] [-j N]"

backups="$REPO_ROOT/backups"
snapshot_container="cms-pgtool-$$-snapshot"
partial=""

# Removing both tool containers also ends the snapshot session.
cleanup() {
  local status=$?
  # A second Ctrl+C must not stop the cleanup half way.
  trap '' INT TERM HUP
  # A docker run stopped while the daemon creates its container ends at once,
  # and the container appears just after (up to 0.6 s with Docker 29), in the
  # Created state that --rm never removes: give it time to appear.
  if (( status > 128 )); then sleep 2; fi
  docker rm -f "cms-pgtool-$$" "$snapshot_container" >/dev/null 2>&1 || true
  # A dump container that was still starting may create .partial only now.
  if [[ -n "${dump_pid:-}" ]]; then wait "$dump_pid" 2>/dev/null || true; fi
  if [[ -n "$partial" ]]; then rm -rf "$partial"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ── 1-2. Connection, network and client image ──────────────────────────────
db_connection_from_env
compose_network >/dev/null

# ensure_image MAJOR — pull postgres:MAJOR if it is missing.
ensure_image() {
  docker image inspect "postgres:$1" >/dev/null 2>&1 && return
  echo "Pulling postgres:$1..."
  docker pull "postgres:$1" || die "could not pull postgres:$1; check the network and run again"
}

ensure_image 17
server=$(pg_tool 17 psql -X -At -F $'\t' \
  -c "SELECT current_setting('server_version_num'), pg_database_size(current_database())") \
  || die "could not query the database server"
read -r version_num db_size <<<"$server"
major="$(pick_pg_major "$version_num")"
ensure_image "$major"

# ── 3. Free space ───────────────────────────────────────────────────────────
mkdir -p "$backups"
chmod 700 "$backups"
free=$(( $(df -Pk "$backups" | awk 'NR == 2 { print $4 }') * 1024 ))
if (( free < db_size )); then
  die "not enough free space in $backups: $(numfmt --to=iec "$free") free," \
      "the database takes $(numfmt --to=iec "$db_size")"
fi

# ── 4. Summary ──────────────────────────────────────────────────────────────
echo "Database:   $PGDATABASE on $PGHOST:$PGPORT (PostgreSQL $(( version_num / 10000 )).$(( version_num % 10000 )))"
echo "Size:       $(numfmt --to=iec "$db_size")"
echo "Free space: $(numfmt --to=iec "$free") in $backups"
echo "Workers:    $workers"
echo "Image:      postgres:$major"
echo "Backup:     $backups/$name"
if ! ask_yes_no "Start the backup?" "n"; then
  echo "Cancelled."
  exit 1
fi
start=$SECONDS

# ── 5. Snapshot session and manifest ────────────────────────────────────────
coproc SESSION {
  PG_TOOL_NAME="$snapshot_container"
  PG_TOOL_ARGS=(-i)
  pg_tool "$major" psql -X -q -At -F $'\t' -v ON_ERROR_STOP=1
}
# Copies, because bash unsets SESSION when the coprocess ends.
session_in="${SESSION[1]}" session_out="${SESSION[0]}" coproc_pid="$SESSION_PID"

# session_query SQL — send SQL to the snapshot session and print the rows it
# returns. Call it inside $(...): if the session is gone, the subshell, not
# the script, gets the SIGPIPE, and the call fails.
session_query() {
  local line
  {
    printf '%s\n' "$1" "SELECT 'end of reply';" >&"$session_in" || return
    while IFS= read -r line <&"$session_out"; do
      if [[ "$line" == "end of reply" ]]; then return 0; fi
      printf '%s\n' "$line"
    done
  } 2>/dev/null
  return 1
}

# Every row is "key<TAB>value"; the table counts come from one generated
# query, so a new table is counted with no list to update. psql and pg_dump
# come from the same image, so VERSION_NAME is pg_dump's version. The
# timeouts are turned off as pg_dump does, so the server keeps the session.
manifest_sql=$(cat <<'SQL'
SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SELECT 'snapshot', pg_export_snapshot();
SELECT 'server_version', current_setting('server_version');
SELECT 'pg_dump_version', :'VERSION_NAME';
SELECT 'database', current_database();
SELECT 'encoding', pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database();
SELECT 'lc_collate', datcollate FROM pg_database WHERE datname = current_database();
SELECT 'lc_ctype', datctype FROM pg_database WHERE datname = current_database();
SELECT 'database_setting', coalesce(r.rolname, '*'), unnest(s.setconfig)
  FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase
  LEFT JOIN pg_roles r ON r.oid = s.setrole WHERE d.datname = current_database();
SELECT 'large_objects', count(*) FROM pg_largeobject_metadata;
SELECT format('SELECT * FROM (%s) AS counts ORDER BY 2', string_agg(
         format('SELECT ''table'', %L, count(*) FROM public.%I', tablename, tablename), ' UNION ALL '))
  FROM pg_tables WHERE schemaname = 'public' \gexec
SQL
)

echo "Taking the snapshot and counting the rows..."
reply=$(session_query "$manifest_sql") || die "the snapshot session failed"
snapshot=$(awk -F'\t' '$1 == "snapshot" { print $2 }' <<<"$reply")
manifest=$(
  printf 'created_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'git_commit\t%s\n' \
    "$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  printf 'fork_version\t%s\n' "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO_ROOT/pyproject.toml")"
  awk -F'\t' '$1 != "snapshot"' <<<"$reply"
)

# ── 6. Dump ─────────────────────────────────────────────────────────────────
partial="$backups/$name.partial"
echo "Dumping with $workers workers..."
# shortcut: no gzip:6 fallback, every official postgres:17+ image has zstd;
# add one if the client image ever changes.
(
  PG_TOOL_ARGS=(-v "$backups:/backups")
  pg_tool "$major" nice -n 10 pg_dump --snapshot="$snapshot" -Fd -j "$workers" \
    --compress=zstd:3 -f "/backups/$name.partial"
) &
dump_pid=$!
# Unlike a foreground command, wait lets the INT and TERM traps run at once.
wait "$dump_pid" || die "pg_dump failed"
# The container's umask is 022: keep every file of the backup to root.
chmod -R go= "$partial"

# ── 7. Close the session and verify ─────────────────────────────────────────
reply=$(session_query "SELECT 1;") \
  || die "the snapshot session ended during the dump; the dump may not match the manifest"
# At end of input psql ends the session, which releases the snapshot.
exec {session_in}>&-
wait "$coproc_pid" || true

echo "Verifying the dump..."
toc=$(PG_TOOL_ARGS=(-v "$backups:/backups"); pg_tool "$major" pg_restore -l "/backups/$name.partial") \
  || die "pg_restore could not read the table of contents"
# TOC lines read "ID; OID OID TABLE DATA public NAME OWNER".
if ! diff <(awk -F'\t' '$1 == "table" { print $2 }' <<<"$manifest" | LC_ALL=C sort) \
          <(awk '$4 == "TABLE" && $5 == "DATA" && $6 == "public" { print $7 }' <<<"$toc" | LC_ALL=C sort) >&2; then
  die "the TABLE DATA entries of the dump (>) differ from the tables of the manifest (<)"
fi
# pg_dump 17+ lists each large object on a line of a blobs_<id>.toc file.
dumped_objects=$(find "$partial" -name 'blobs*.toc' -exec cat {} + | wc -l)
manifest_objects=$(awk -F'\t' '$1 == "large_objects" { print $2 }' <<<"$manifest")
if (( dumped_objects != manifest_objects )); then
  die "the dump holds $dumped_objects large objects, the manifest $manifest_objects"
fi

# ── 8. Manifest, checksums, rename ──────────────────────────────────────────
printf '%s\n' "$manifest" >"$partial/MANIFEST"
echo "Writing SHA256SUMS..."
# stdbuf -oL: one write per line, so the parallel sha256sum lines never mix.
(cd "$partial" && find . -type f ! -name SHA256SUMS -printf '%P\0' \
  | xargs -0 -r -P "$(nproc)" -n 32 stdbuf -oL sha256sum) >"$partial/SHA256SUMS"
mv -T "$partial" "$backups/$name"
partial=""

# ── 9. Done ─────────────────────────────────────────────────────────────────
echo ""
echo "Backup complete: $backups/$name"
echo "Size:     $(du -sh "$backups/$name" | cut -f1)"
echo "Duration: $(( SECONDS - start )) s"
echo "Copy it off the server, from your machine:"
echo "  rsync -a --rsync-path=\"sudo rsync\" ${SUDO_USER:-$(id -un)}@$HOSTNAME:$backups/$name ./"
echo "Reminder: .env is not inside the backup; keep a copy of it somewhere safe."
