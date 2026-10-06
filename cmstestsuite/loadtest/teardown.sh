#!/usr/bin/env bash
# Stop and remove the load-test stack and its volumes (images are kept;
# pass --images to remove them too).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
docker --context default compose -p cmsload-run -f "$HERE/compose.yml" --env-file "$HERE/.env.load" down -v --remove-orphans
if [[ "${1:-}" == "--images" ]]; then
  docker --context default image rm cmsload-cms:latest cmsload-ranking:latest cmsload-driver:latest || true
fi
