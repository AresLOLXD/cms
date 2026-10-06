#!/usr/bin/env bash
# Stop and remove the load-test stack of one target and its volumes (the
# image is kept; pass --images to remove it too).
#   ./teardown.sh fork
#   ./teardown.sh upstream --images
set -euo pipefail
TARGET=${1:?target: fork or upstream}
[[ $TARGET == fork || $TARGET == upstream ]] || { echo "unknown target $TARGET" >&2; exit 2; }
[[ -z ${2:-} || ${2:-} == --images ]] || { echo "unknown option ${2:-}" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
# "down" finds the stack by its project name; compose only needs these
# variables to read the file.
export LOAD_TARGET=$TARGET LOAD_DB_PASSWORD=unused LOAD_WORKER_COUNT=1 LOAD_CWS_COUNT=1
dc() { sg docker -c "$(printf '%q ' docker --context default "$@")"; }
dc compose -p "cmsload-$TARGET" -f "$HERE/compose.yml" down -v --remove-orphans
if [[ ${2:-} == --images ]]; then
  dc image rm "cmsload-$TARGET:latest" || true
fi
