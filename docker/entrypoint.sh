#!/bin/bash
set -euo pipefail

# Generate cms.toml (skipped if CMS_CONFIG already points to an existing file),
# cms_ranking.toml, and supervisord.conf (only when CMS_CONTEST_ID is set).
python3 /home/cmsuser/generate_config.py

# Validate that the config file parses without errors before starting any service.
python3 -c "import cms.conf" 2>&1 || {
    echo "ERROR: CMS config failed to parse. Check your environment variables." >&2
    exit 1
}

# Remove stale socket files left by previous container runs.
rm -f /home/cmsuser/cms/run/*.sock 2>/dev/null || true

exec "$@"
