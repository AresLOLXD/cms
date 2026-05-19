#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

echo "Project:    $(_env_var CMS_PROJECT_NAME cms-prod)"
echo "Contest ID: $(_env_var CMS_CONTEST_ID "(none)")"
echo ""

echo "── Container status ─────────────────────────────────"
"${COMPOSE_CMD[@]}" ps
echo ""

echo "── Supervisor status ────────────────────────────────"
"${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" status 2>/dev/null \
  || echo "(container not running)"
