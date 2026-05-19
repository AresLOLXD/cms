#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

has_tail=false
for arg in "$@"; do
  [[ "$arg" == --tail* ]] && has_tail=true && break
done

tail_args=()
$has_tail || tail_args=(--tail 100)

"${COMPOSE_CMD[@]}" logs "${tail_args[@]}" "$@"
