#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=docker/_lib.sh
source "$SCRIPT_DIR/docker/_lib.sh"

ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env file not found at $ENV_FILE"
  echo "Copy .env.example to .env and fill in the required values first."
  exit 1
fi

# Show current contest ID
current_id=$(grep -m1 -E '^CMS_CONTEST_ID=' "$ENV_FILE" | cut -d= -f2 || echo "not set")
echo "Current contest ID: $current_id"
echo ""

# Try to list available contests from the running db container
ADMIN_PORT="$(_env_var CMS_AWS_HTTP_PORT 8889)"
DB_USER="$(_env_var POSTGRES_USER cms)"
DB_NAME="$(_env_var POSTGRES_DB cmsdb)"

echo "Fetching contests from database..."
CONTESTS=$(
  "${COMPOSE_CMD[@]}" exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
    -c "SELECT id, name FROM contests ORDER BY id;" \
    2>/dev/null
) || CONTESTS=""

if [[ -z "$CONTESTS" || "$CONTESTS" == *"(0 rows)"* ]]; then
  echo "No contests found in the database."
  echo "Create one from the Admin interface: http://localhost:${ADMIN_PORT}"
  echo ""
else
  echo "Available contests:"
  echo "$CONTESTS"
  echo ""
fi

# Prompt for new ID
while true; do
  read -r -p "Enter new contest ID: " new_id
  if [[ "$new_id" =~ ^[0-9]+$ && "$new_id" -gt 0 ]]; then
    break
  fi
  echo "Please enter a positive integer (e.g. 1, 2, 3)."
done

# Update .env
if grep -qE '^CMS_CONTEST_ID=' "$ENV_FILE"; then
  sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=${new_id}/" "$ENV_FILE"
else
  echo "CMS_CONTEST_ID=${new_id}" >> "$ENV_FILE"
fi

echo "Contest ID updated to ${new_id} in .env"

if ask_yes_no "Restart services to apply change?" "n"; then
  bash "$SCRIPT_DIR/restart.sh"
fi
