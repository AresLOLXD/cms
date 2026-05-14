#!/usr/bin/env bash
# Automated smoke tests for docker/_lib.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

check() {
  local desc="$1" result="$2"
  if [[ "$result" == "ok" ]]; then
    echo "  PASS: $desc"
    ((PASS++)) || true
  else
    echo "  FAIL: $desc — $result"
    ((FAIL++)) || true
  fi
}

echo "=== _lib.sh tests ==="

# ── ask_yes_no ────────────────────────────────────────────────────────────────

if echo "y" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' n" >/dev/null 2>&1; then
  check "ask_yes_no: 'y' returns 0" "ok"
else
  check "ask_yes_no: 'y' returns 0" "returned non-zero"
fi

if echo "n" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' y" >/dev/null 2>&1; then
  check "ask_yes_no: 'n' returns 1" "returned zero (expected non-zero)"
else
  check "ask_yes_no: 'n' returns 1" "ok"
fi

if echo "" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' n" >/dev/null 2>&1; then
  check "ask_yes_no: empty input uses default 'n' (returns 1)" "returned zero"
else
  check "ask_yes_no: empty input uses default 'n' (returns 1)" "ok"
fi

if echo "" | bash -c "source '$REPO_ROOT/docker/_lib.sh'; ask_yes_no 'q?' y" >/dev/null 2>&1; then
  check "ask_yes_no: empty input uses default 'y' (returns 0)" "ok"
else
  check "ask_yes_no: empty input uses default 'y' (returns 0)" "returned non-zero"
fi

# ── PROJECT_NAME ──────────────────────────────────────────────────────────────

result=$(CMS_PROJECT_NAME="" bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$PROJECT_NAME")
if [[ "$result" == "cms-prod" ]]; then
  check "PROJECT_NAME defaults to 'cms-prod'" "ok"
else
  check "PROJECT_NAME defaults to 'cms-prod'" "got '$result'"
fi

result=$(CMS_PROJECT_NAME="my-contest" bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$PROJECT_NAME")
if [[ "$result" == "my-contest" ]]; then
  check "PROJECT_NAME reads CMS_PROJECT_NAME from environment" "ok"
else
  check "PROJECT_NAME reads CMS_PROJECT_NAME from environment" "got '$result'"
fi

# ── COMPOSE_CMD ───────────────────────────────────────────────────────────────

result=$(bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \$COMPOSE_CMD")
if [[ "$result" == *"docker compose"* && "$result" == *"docker-compose.prod.yml"* ]]; then
  check "COMPOSE_CMD contains 'docker compose' and compose file path" "ok"
else
  check "COMPOSE_CMD contains 'docker compose' and compose file path" "got '$result'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
