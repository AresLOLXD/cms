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
# supervisorctl status exits 3 when any program is not RUNNING (one you
# stopped on purpose included), and it has no timeout of its own: it
# hangs on a stopped supervisord.
status=0
"${COMPOSE_CMD[@]}" exec -T cms timeout 10 "${SUPERVISORCTL[@]}" status \
  2>/dev/null || status=$?
case $status in
  0 | 3) ;;
  124) echo "(supervisord did not answer in 10 s)" ;;
  *) echo "(container not running)" ;;
esac
