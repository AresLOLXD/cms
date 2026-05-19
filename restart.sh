#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

if ! ask_yes_no "Restart all services?" "n"; then
  echo "Aborted."
  exit 0
fi

echo "Stopping services..."
"${COMPOSE_CMD[@]}" down

echo ""
echo "Starting services..."
_do_up

echo ""
"${COMPOSE_CMD[@]}" ps
