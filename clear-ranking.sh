#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

RANKING_LIB="/home/cmsuser/cms/lib/ranking"

CLEAR_RESULTS=false
CLEAR_USERS=false
CLEAR_CONTESTS=false
REGENERATE=false

if ask_yes_no "Clear results? (submissions and subchanges)" "n"; then CLEAR_RESULTS=true; fi
if ask_yes_no "Clear users?" "n"; then CLEAR_USERS=true; fi
if ask_yes_no "Clear tasks and contests?" "n"; then CLEAR_CONTESTS=true; fi
if ask_yes_no "Regenerate ranking from current contest data?" "n"; then REGENERATE=true; fi

if ! $CLEAR_RESULTS && ! $CLEAR_USERS && ! $CLEAR_CONTESTS && ! $REGENERATE; then
  echo "Nothing selected, exiting."
  exit 0
fi

# Summary of selected actions
echo ""
echo "Actions selected:"
if $CLEAR_RESULTS;  then echo "  - Clear results (submissions, subchanges)"; fi
if $CLEAR_USERS;    then echo "  - Clear users"; fi
if $CLEAR_CONTESTS; then echo "  - Clear tasks and contests"; fi
if $REGENERATE;     then echo "  - Regenerate ranking from contest data (~6 min)"; fi
echo ""

if $CLEAR_CONTESTS && ! $CLEAR_RESULTS; then
  echo "Warning: clearing tasks/contests without clearing results will leave orphaned"
  echo "submission records in the ranking. Consider also clearing results."
  echo ""
fi

if $CLEAR_RESULTS && ! $REGENERATE; then
  echo "Warning: clearing results without regenerating may cause submissions scored"
  echo "while the ranking is down to not reappear. Consider enabling regenerate."
  echo ""
fi

if ! ask_yes_no "Proceed? This cannot be undone." "n"; then
  echo "Aborted."
  exit 0
fi

# Check that the cms container is running before touching anything
if ! "${COMPOSE_CMD[@]}" exec -T cms true 2>/dev/null; then
  echo "ERROR: cms container is not running. Start it first with ./up.sh" >&2
  exit 1
fi

if $CLEAR_RESULTS || $CLEAR_USERS || $CLEAR_CONTESTS; then
  echo "Stopping ranking server..."
  "${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" stop cmsrankingwebserver

  # If anything below fails, bring the ranking server back up before exiting
  _on_error() {
    echo "ERROR: operation failed. Attempting to restart ranking server..." >&2
    "${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" start cmsrankingwebserver 2>/dev/null || true
  }
  trap _on_error ERR

  FIND_DIRS=()
  if $CLEAR_RESULTS;  then FIND_DIRS+=("$RANKING_LIB/submissions" "$RANKING_LIB/subchanges"); fi
  if $CLEAR_USERS;    then FIND_DIRS+=("$RANKING_LIB/users"); fi
  if $CLEAR_CONTESTS; then FIND_DIRS+=("$RANKING_LIB/tasks" "$RANKING_LIB/contests"); fi

  for dir in "${FIND_DIRS[@]}"; do
    "${COMPOSE_CMD[@]}" exec -T cms find "$dir" -maxdepth 1 -name '*.json' -delete 2>/dev/null || true
  done

  echo "Starting ranking server..."
  "${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" start cmsrankingwebserver

  trap - ERR

  # Wait for the ranking server to be ready before triggering proxy restart
  RWS_PORT="$(_env_var CMS_RWS_HTTP_PORT 8890)"
  echo "Waiting for ranking server to be ready..."
  for _ in $(seq 1 15); do
    if "${COMPOSE_CMD[@]}" exec -T cms curl -sf "http://localhost:${RWS_PORT}/" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

if $REGENERATE; then
  echo "Restarting proxy service..."
  "${COMPOSE_CMD[@]}" exec -T cms "${SUPERVISORCTL[@]}" restart cmsproxyservice
  echo "Scores will appear in the ranking within ~6 minutes."
fi
