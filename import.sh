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
  cmsDumpImporter "${DROP_FLAGS[@]+"${DROP_FLAGS[@]}"}" "${EXCL_FLAGS[@]+"${EXCL_FLAGS[@]}"}" \
  "/home/cmsuser/cms/dumps/${selected}"
