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

echo ""
echo "── Watchdog ─────────────────────────────────────────"
"${COMPOSE_CMD[@]}" exec -T cms timeout 10 cmsWatchdog --status \
  2>/dev/null || echo "(no answer from the cms container)"

echo ""
echo "── Disk ─────────────────────────────────────────────"
# df -P puts each filesystem on one line: the figures are on the second.
if disk=$("${COMPOSE_CMD[@]}" exec -T cms timeout 10 df -P /home/cmsuser/cms/log \
  2>/dev/null); then
  echo "$disk"
  use=$(awk 'NR == 2 { sub("%", "", $5); print $5 }' <<<"$disk")
  if [[ "$use" =~ ^[0-9]+$ ]] && ((use >= 80)); then
    echo "WARNING: the log disk is ${use}% full"
  fi
else
  echo "(no answer from the cms container)"
fi
