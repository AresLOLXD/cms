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

_CWS_BASE_PORT="$(_env_var CMS_CWS_HTTP_PORT 8888)"
_CWS_COUNT="$(_env_var CMS_CWS_COUNT 1)"
if (( _CWS_COUNT < 1 )); then
  echo "ERROR: CMS_CWS_COUNT must be >= 1 (got: ${_CWS_COUNT})" >&2
  exit 1
fi
export CMS_CWS_HTTP_PORT_END=$(( _CWS_BASE_PORT + _CWS_COUNT - 1 ))

# Append --profile localdb if CMS_USE_LOCALDB=true is persisted in .env
if [[ "$(_env_var CMS_USE_LOCALDB false)" == "true" ]]; then
  COMPOSE_CMD+=(--profile localdb)
fi

# shellcheck disable=SC2034  # used by scripts that source this file
SUPERVISORCTL=(supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf)

# ask_yes_no "Question text?" "y|n"
# Prints an interactive prompt. Returns 0 for yes, 1 for no.
ask_yes_no() {
  local question="$1" default="${2:-n}" prompt answer
  [[ "$default" == "y" ]] && prompt="[Y/n]" || prompt="[y/N]"
  while true; do
    read -r -p "$question $prompt " answer || break
    answer="${answer:-$default}"
    case "${answer,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
  [[ "${default,,}" == "y" ]] && return 0 || return 1
}

# _set_env_var KEY VALUE — atomically write or update KEY=VALUE in .env
_set_env_var() {
  local key="$1" value="$2"
  local env_file="$REPO_ROOT/.env"
  local tmp_file
  tmp_file=$(mktemp "${env_file}.XXXXXX")
  local escaped_value
  escaped_value=$(printf '%s' "$value" | sed 's/[|&\]/\\&/g')
  if grep -qE "^${key}=" "$env_file" 2>/dev/null; then
    sed "s|^${key}=.*|${key}=${escaped_value}|" "$env_file" > "$tmp_file"
  else
    { cat "$env_file" 2>/dev/null; echo "${key}=${value}"; } > "$tmp_file"
  fi
  mv "$tmp_file" "$env_file"
}

# _do_up — Docker preflight, localdb choice (persisted), selective rebuild, compose up --wait
_do_up() {
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running or not accessible." >&2
    exit 1
  fi

  local up_cmd=(docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" -p "$PROJECT_NAME")

  local current_localdb
  current_localdb="$(_env_var CMS_USE_LOCALDB false)"
  local localdb_default
  [[ "$current_localdb" == "true" ]] && localdb_default="y" || localdb_default="n"

  if ask_yes_no "Use local PostgreSQL container?" "$localdb_default"; then
    _set_env_var "CMS_USE_LOCALDB" "true"
    up_cmd+=(--profile localdb)
    COMPOSE_CMD+=(--profile localdb)
  else
    _set_env_var "CMS_USE_LOCALDB" "false"
  fi

  local choice
  printf "Rebuild?\n  1) No  (default)\n  2) All services\n  3) Ranking only\n  4) CMS only\n  5) CMS only (no cache)\n  6) Ranking only (no cache)\n  7) All services (no cache)\n"
  read -r -p "Choice [1-7]: " choice
  choice="${choice:-1}"

  case "$choice" in
    2) "${up_cmd[@]}" build ;;
    3) "${up_cmd[@]}" build ranking ;;
    4) "${up_cmd[@]}" build cms db-init ;;
    5) "${up_cmd[@]}" build --no-cache cms db-init ;;
    6) "${up_cmd[@]}" build --no-cache ranking ;;
    7) "${up_cmd[@]}" build --no-cache ;;
  esac

  "${up_cmd[@]}" up -d --wait --wait-timeout 90
}

# _run_timed LABEL CMD [ARGS...] — run CMD streaming output, print elapsed time.
_run_timed() {
  local label="$1"
  shift
  local start=$SECONDS
  echo "$label"
  "$@"
  local rc=$? elapsed=$(( SECONDS - start ))
  if [[ $rc -eq 0 ]]; then
    printf "Done in %ds.\n" "$elapsed"
  else
    printf "Failed after %ds.\n" "$elapsed" >&2
  fi
  return $rc
}
