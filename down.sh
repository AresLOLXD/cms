#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

if ! ask_yes_no "Stop all services? Contestants will be disconnected." "n"; then
  echo "Aborted."
  exit 0
fi

"${COMPOSE_CMD[@]}" down
