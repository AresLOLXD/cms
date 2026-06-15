#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

mkdir -p "$REPO_ROOT/dumps"
chown 2000:2000 "$REPO_ROOT/dumps"

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
default_name="export-$(date +%Y-%m-%d).tar.gz"
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
  cmsDumpExporter "${CONTEST_FLAGS[@]+"${CONTEST_FLAGS[@]}"}" "${EXCL_FLAGS[@]+"${EXCL_FLAGS[@]}"}" \
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
