#!/usr/bin/env bash
# docker/_lib.sh — shared variables and helpers. Source this; do not run directly.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read a variable: env takes priority, then .env file, then the given default.
_env_var() {
  local name="$1" default="$2"
  local val="${!name:-}"
  if [[ -z "$val" && -f "$REPO_ROOT/.env" ]]; then
    val=$(grep -m1 -E "^${name}=" "$REPO_ROOT/.env" | cut -d= -f2-)
    val="${val#\"}" ; val="${val%\"}"   # strip surrounding double-quotes if present
  fi
  echo "${val:-$default}"
}

PROJECT_NAME="$(_env_var CMS_PROJECT_NAME cms-prod)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.prod.yml"
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" -p "$PROJECT_NAME")

# ask_yes_no "Question text?" "y|n"
# Prints an interactive prompt. Returns 0 for yes, 1 for no.
ask_yes_no() {
  local question="$1" default="${2:-n}" prompt answer
  [[ "$default" == "y" ]] && prompt="[Y/n]" || prompt="[y/N]"
  while true; do
    read -r -p "$question $prompt " answer
    answer="${answer:-$default}"
    case "${answer,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}
