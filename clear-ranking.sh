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

if $CLEAR_CONTESTS && ! $CLEAR_RESULTS; then
  echo "Warning: clearing tasks and contests without clearing results will leave"
  echo "orphaned submission records in the ranking. Consider also clearing results."
fi

if ! ask_yes_no "Proceed? This cannot be undone." "n"; then
  echo "Aborted."
  exit 0
fi

if $CLEAR_RESULTS || $CLEAR_USERS || $CLEAR_CONTESTS; then
  echo "Stopping ranking server..."
  "${COMPOSE_CMD[@]}" exec -T cms supervisorctl stop cmsrankingwebserver

  DELETE_CMD="rm -f"
  if $CLEAR_RESULTS; then
    DELETE_CMD="$DELETE_CMD '${RANKING_LIB}/submissions/*.json' '${RANKING_LIB}/subchanges/*.json'"
  fi
  if $CLEAR_USERS; then
    DELETE_CMD="$DELETE_CMD '${RANKING_LIB}/users/*.json'"
  fi
  if $CLEAR_CONTESTS; then
    DELETE_CMD="$DELETE_CMD '${RANKING_LIB}/tasks/*.json' '${RANKING_LIB}/contests/*.json'"
  fi
  "${COMPOSE_CMD[@]}" exec -T cms sh -c "$DELETE_CMD"

  echo "Starting ranking server..."
  "${COMPOSE_CMD[@]}" exec -T cms supervisorctl start cmsrankingwebserver
fi

if $REGENERATE; then
  echo "Restarting proxy service..."
  "${COMPOSE_CMD[@]}" exec -T cms supervisorctl restart cmsproxyservice
  echo "Scores will appear in the ranking within ~6 minutes."
fi
