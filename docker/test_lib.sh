#!/usr/bin/env bash
# Automated smoke tests for docker/_lib.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

result=$(bash -c "source '$REPO_ROOT/docker/_lib.sh'; echo \"\${COMPOSE_CMD[*]}\"")
if [[ "$result" == *"docker compose"* && "$result" == *"docker-compose.prod.yml"* && "$result" == *"--env-file"* && "$result" == *"-p"* ]]; then
  check "COMPOSE_CMD contains docker compose, compose file, --env-file, and -p" "ok"
else
  check "COMPOSE_CMD contains docker compose, compose file, --env-file, and -p" "got '$result'"
fi

# ── localdb default ───────────────────────────────────────────────────────────

result=$(CMS_USE_LOCALDB=true bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "true" ]]; then
  check "_env_var CMS_USE_LOCALDB returns 'true' when set in env" "ok"
else
  check "_env_var CMS_USE_LOCALDB returns 'true' when set in env" "got '$result'"
fi

result=$(CMS_USE_LOCALDB=false bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "false" ]]; then
  check "_env_var CMS_USE_LOCALDB returns 'false' when set to false" "ok"
else
  check "_env_var CMS_USE_LOCALDB returns 'false' when set to false" "got '$result'"
fi

result=$(CMS_USE_LOCALDB="" bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "false" ]]; then
  check "_env_var CMS_USE_LOCALDB falls back to default when unset" "ok"
else
  check "_env_var CMS_USE_LOCALDB falls back to default when unset" "got '$result'"
fi

# ── _do_up: ranking-only options ──────────────────────────────────────────────
# Run _do_up against a stub "docker" that records its arguments, in a scratch
# copy of the repo layout (so the .env that _do_up writes is not the real one).

TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir -p "$TMP_ROOT/docker" "$TMP_ROOT/bin"
cp "$REPO_ROOT/docker/_lib.sh" "$TMP_ROOT/docker/_lib.sh"
printf '#!/usr/bin/env bash\necho "$*" >> "$DOCKER_CALLS"\n' > "$TMP_ROOT/bin/docker"
chmod +x "$TMP_ROOT/bin/docker"

# run_do_up ANSWER... — feed the answers (local database, rebuild choice) to
# _do_up; docker calls end up in $TMP_ROOT/calls, its output in $DO_UP_OUT.
run_do_up() {
  : > "$TMP_ROOT/calls"
  DO_UP_OUT=$(printf '%s\n' "$@" | DOCKER_CALLS="$TMP_ROOT/calls" PATH="$TMP_ROOT/bin:$PATH" \
    bash -c "source '$TMP_ROOT/docker/_lib.sh'; _do_up" 2>&1)
}

for entry in "3:build ranking" "6:build --no-cache ranking"; do
  choice="${entry%%:*}" build="${entry#*:}"
  run_do_up n "$choice"
  up_calls=$(grep -E ' up ' "$TMP_ROOT/calls" || true)
  if grep -q " ${build}\$" "$TMP_ROOT/calls" \
      && [[ "$(wc -l <<<"$up_calls")" -eq 1 && "$up_calls" == *" up -d --no-deps --wait --wait-timeout 90 ranking" ]] \
      && [[ "$DO_UP_OUT" == *"Only the ranking container was started or updated"* ]]; then
    check "_do_up choice $choice builds and starts only ranking, and says so" "ok"
  else
    check "_do_up choice $choice builds and starts only ranking, and says so" "calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
  fi
done

for entry in "1:" "2:build" "4:build cms db-init" "7:build --no-cache"; do
  choice="${entry%%:*}" build="${entry#*:}"
  run_do_up n "$choice"
  up_calls=$(grep -E ' up ' "$TMP_ROOT/calls" || true)
  if [[ ( -z "$build" ) || "$(grep -c " ${build}\$" "$TMP_ROOT/calls")" -eq 1 ]] \
      && [[ "$(wc -l <<<"$up_calls")" -eq 1 && "$up_calls" == *" up -d --wait --wait-timeout 90" ]] \
      && [[ "$DO_UP_OUT" != *"Only the ranking container"* ]]; then
    check "_do_up choice $choice still starts every service" "ok"
  else
    check "_do_up choice $choice still starts every service" "calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
