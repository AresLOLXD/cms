#!/bin/bash
set -euo pipefail

python3 /home/cmsuser/generate_config.py

rm -f /home/cmsuser/cms/run/*.sock 2>/dev/null || true

exec "$@"
