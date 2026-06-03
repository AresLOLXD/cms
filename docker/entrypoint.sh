#!/bin/bash
set -euo pipefail

# Generate cms.toml (skipped if CMS_CONFIG already points to an existing file),
# cms_ranking.toml, and supervisord.conf (only when CMS_CONTEST_ID is set).
# Set CMS_RANKING_ONLY=true to write only cms_ranking.toml (ranking container).
python3 /home/cmsuser/generate_config.py

# Validate that the config file parses without errors before starting any service.
if ! python3 -c "import cms.conf"; then
    echo "ERROR: CMS config failed to parse. Check your environment variables." >&2
    exit 1
fi

# Remove stale socket files left by previous container runs.
rm -f /home/cmsuser/cms/run/*.sock 2>/dev/null || true

exec "$@"
