#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

GLOBAL_ARGS=()
UP_ARGS=()

if ask_yes_no "Use local database (Docker)?" "n"; then
  GLOBAL_ARGS+=(--profile localdb)
fi

if ask_yes_no "Rebuild image?" "n"; then
  UP_ARGS+=(--build)
fi

"${COMPOSE_CMD[@]}" "${GLOBAL_ARGS[@]}" up -d "${UP_ARGS[@]}"
