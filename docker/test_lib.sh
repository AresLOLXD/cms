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
# DOCKER_FAIL_ON: a call whose arguments contain this text fails (after it is recorded).
# shellcheck disable=SC2016  # the stub's own variables must stay unexpanded
printf '#!/usr/bin/env bash\necho "$*" >> "$DOCKER_CALLS"\necho "$GIT_SHA" >> "$DOCKER_CALLS.sha"\nif [[ -n "${DOCKER_FAIL_ON:-}" && " $* " == *"$DOCKER_FAIL_ON"* ]]; then exit 1; fi\n' > "$TMP_ROOT/bin/docker"
chmod +x "$TMP_ROOT/bin/docker"

# run_do_up ANSWER... — feed the answers (local database, rebuild choice) to
# _do_up; docker calls end up in $TMP_ROOT/calls, its output (stdout, then
# stderr) in $DO_UP_OUT, its stderr alone in $DO_UP_ERR and its exit status in
# $DO_UP_RC.
run_do_up() {
  : > "$TMP_ROOT/calls"
  : > "$TMP_ROOT/calls.sha"
  DO_UP_RC=0
  # Like a real deployment: a .env exists (under set -e, _set_env_var cannot create one).
  [[ -f "$TMP_ROOT/.env" ]] || : > "$TMP_ROOT/.env"
  DO_UP_OUT=$(printf '%s\n' "$@" | DOCKER_CALLS="$TMP_ROOT/calls" PATH="$TMP_ROOT/bin:$PATH" \
    bash -c "set -euo pipefail; source '$TMP_ROOT/docker/_lib.sh'; _do_up" 2>"$TMP_ROOT/stderr") || DO_UP_RC=$?
  DO_UP_ERR=$(<"$TMP_ROOT/stderr")
  DO_UP_OUT+="$DO_UP_ERR"
}

# Scenario: The old choices are unchanged
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

# Scenario: The old choices are unchanged
for entry in "1:" "2:build" "4:build cms db-init" "5:build --no-cache cms db-init" "7:build --no-cache"; do
  choice="${entry%%:*}" build="${entry#*:}"
  run_do_up n "$choice"
  up_calls=$(grep -E ' up ' "$TMP_ROOT/calls" || true)
  build_calls=$(grep -E ' build( |$)' "$TMP_ROOT/calls" || true)
  # Choice 1 must not build at all; the others build exactly once, as listed.
  if [[ ( -z "$build" && -z "$build_calls" ) \
        || ( -n "$build" && "$build_calls" == *" ${build}" && "$(wc -l <<<"$build_calls")" -eq 1 ) ]] \
      && [[ "$(wc -l <<<"$up_calls")" -eq 1 && "$up_calls" == *" up -d --wait --wait-timeout 90" ]] \
      && [[ "$DO_UP_OUT" != *"Only the ranking container"* ]]; then
    check "_do_up choice $choice still starts every service" "ok"
  else
    check "_do_up choice $choice still starts every service" "calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
  fi
done

# ── _do_up: choice 8, ranking then CMS ────────────────────────────────────────

# Scenario: The menu lists the new choice
run_do_up n 1
if [[ "$DO_UP_OUT" == *"8) Ranking, then CMS (safe order)"* ]] && grep -q 'Choice \[1-8\]' "$REPO_ROOT/docker/_lib.sh"; then
  check "_do_up lists choice 8 and prompts for 1-8" "ok"
else
  check "_do_up lists choice 8 and prompts for 1-8" "out: $DO_UP_OUT"
fi

# Scenario: Order of build and start
run_do_up n 8
build_line=$(grep -nE ' build ranking cms db-init$' "$TMP_ROOT/calls" | cut -d: -f1 || true)
ranking_line=$(grep -nE ' up -d --no-deps --wait --wait-timeout 90 ranking$' "$TMP_ROOT/calls" | cut -d: -f1 || true)
rest_line=$(grep -nE ' up -d --wait --wait-timeout 90$' "$TMP_ROOT/calls" | cut -d: -f1 || true)
if [[ "$DO_UP_RC" -eq 0 && "$(grep -cE ' build ' "$TMP_ROOT/calls")" -eq 1 && "$(grep -cE ' up ' "$TMP_ROOT/calls")" -eq 2 \
      && -n "$build_line" && -n "$ranking_line" && -n "$rest_line" \
      && "$build_line" -lt "$ranking_line" && "$ranking_line" -lt "$rest_line" ]]; then
  check "_do_up choice 8 builds ranking, cms and db-init, then starts the ranking, then the rest" "ok"
else
  check "_do_up choice 8 builds ranking, cms and db-init, then starts the ranking, then the rest" "rc=$DO_UP_RC calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
fi

# Scenario: A failed build touches nothing
DOCKER_FAIL_ON=" build " run_do_up n 8
if [[ "$DO_UP_RC" -ne 0 ]] && ! grep -qE ' up ' "$TMP_ROOT/calls"; then
  check "_do_up choice 8 stops before any start when the build fails" "ok"
else
  check "_do_up choice 8 stops before any start when the build fails" "rc=$DO_UP_RC calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
fi

# Scenario: The ranking does not come up
DOCKER_FAIL_ON=" --no-deps " run_do_up n 8
if [[ "$DO_UP_RC" -ne 0 ]] \
    && [[ "$DO_UP_ERR" == *"ERROR: the ranking did not come up; the CMS was not touched."* ]] \
    && [[ "$(grep -cE ' up ' "$TMP_ROOT/calls")" -eq 1 ]] \
    && ! grep -qE ' up -d --wait --wait-timeout 90$' "$TMP_ROOT/calls"; then
  check "_do_up choice 8 stops with an error when the ranking does not come up" "ok"
else
  check "_do_up choice 8 stops with an error when the ranking does not come up" "rc=$DO_UP_RC err: $DO_UP_ERR calls: $(tr '\n' '|' < "$TMP_ROOT/calls")"
fi

# Scenario: A crash-looping ranking counts as not up
# "up --wait" only fails for a crash loop when the service has a healthcheck
# (measured on a real Docker, see the up-safe-order design), so the ranking
# service must keep one. The real compose run is a manual check.
ranking_block=$(awk '/^  ranking:/{f=1;next} f&&/^  [a-z]/{exit} f' "$REPO_ROOT/docker/docker-compose.prod.yml")
if grep -q '^    healthcheck:' <<<"$ranking_block" && grep -q 'urllib' <<<"$ranking_block"; then
  check "the ranking service has an HTTP healthcheck" "ok"
else
  check "the ranking service has an HTTP healthcheck" "no healthcheck with urllib in the ranking service"
fi

# Scenario: One revision for both steps
run_do_up n 8
if [[ "$(grep -c . "$TMP_ROOT/calls.sha")" -ge 3 && "$(grep . "$TMP_ROOT/calls.sha" | sort -u | wc -l)" -eq 1 ]]; then
  check "_do_up choice 8 uses one GIT_SHA for every compose command" "ok"
else
  check "_do_up choice 8 uses one GIT_SHA for every compose command" "shas: $(tr '\n' '|' < "$TMP_ROOT/calls.sha")"
fi

# ── _do_up: local database (profile localdb) ─────────────────────────────────
# Answering "y" to the local database question must add --profile localdb to
# every compose call _do_up makes and persist CMS_USE_LOCALDB=true in .env;
# answering "n" must do neither (persisting false instead).

# Scenario: The local database applies to both steps
for choice in 1 2 3 8; do
  rm -f "$TMP_ROOT/.env"
  run_do_up y "$choice"
  compose_calls=$(grep -E '^compose ' "$TMP_ROOT/calls" || true)
  without_profile=$(grep -E '^compose ' "$TMP_ROOT/calls" | grep -v -e '--profile localdb' || true)
  if [[ -n "$compose_calls" && -z "$without_profile" ]] \
      && grep -qx 'CMS_USE_LOCALDB=true' "$TMP_ROOT/.env"; then
    check "_do_up choice $choice with the local database uses --profile localdb and persists it" "ok"
  else
    check "_do_up choice $choice with the local database uses --profile localdb and persists it" "calls: $(tr '\n' '|' < "$TMP_ROOT/calls"); .env: $(tr '\n' '|' < "$TMP_ROOT/.env")"
  fi
done

rm -f "$TMP_ROOT/.env"
run_do_up n 2
if ! grep -q -e '--profile' "$TMP_ROOT/calls" && grep -qx 'CMS_USE_LOCALDB=false' "$TMP_ROOT/.env"; then
  check "_do_up without the local database adds no profile and persists false" "ok"
else
  check "_do_up without the local database adds no profile and persists false" "calls: $(tr '\n' '|' < "$TMP_ROOT/calls"); .env: $(tr '\n' '|' < "$TMP_ROOT/.env")"
fi

# ── _do_up: leftover CMS_LOADER_* variables ───────────────────────────────────
# A leftover CMS_LOADER_SESSION_SECRET in .env warns but never stops the deploy.

printf 'CMS_LOADER_SESSION_SECRET=leftover\n' > "$TMP_ROOT/.env"
run_do_up n 1
if [[ "$DO_UP_OUT" == *"CMS-Loader was removed"* ]] && grep -qE ' up ' "$TMP_ROOT/calls"; then
  check "_do_up warns about a leftover CMS_LOADER_SESSION_SECRET and still deploys" "ok"
else
  check "_do_up warns about a leftover CMS_LOADER_SESSION_SECRET and still deploys" "out: $DO_UP_OUT"
fi

rm -f "$TMP_ROOT/.env"
run_do_up n 1
if [[ "$DO_UP_OUT" != *"CMS-Loader"* ]]; then
  check "_do_up does not warn without CMS_LOADER_SESSION_SECRET" "ok"
else
  check "_do_up does not warn without CMS_LOADER_SESSION_SECRET" "out: $DO_UP_OUT"
fi

# ── _do_up: GIT_SHA build arg ─────────────────────────────────────────────────
# The image revision label comes from the repo's HEAD, or "unknown" outside git.

# GIT_CEILING_DIRECTORIES: a TMPDIR inside a git work tree must not leak its HEAD.
GIT_CEILING_DIRECTORIES="$(dirname "$TMP_ROOT")" run_do_up n 1
if [[ "$(tail -n1 "$TMP_ROOT/calls.sha")" == "unknown" ]]; then
  check "_do_up exports GIT_SHA=unknown outside a git repo" "ok"
else
  check "_do_up exports GIT_SHA=unknown outside a git repo" "got '$(tail -n1 "$TMP_ROOT/calls.sha")'"
fi

git -C "$TMP_ROOT" init -q
git -C "$TMP_ROOT" -c user.name=t -c user.email=t@t commit -q --allow-empty -m t
head_sha=$(git -C "$TMP_ROOT" rev-parse HEAD)
run_do_up n 1
if [[ "$(tail -n1 "$TMP_ROOT/calls.sha")" == "$head_sha" ]]; then
  check "_do_up exports GIT_SHA=HEAD inside a git repo" "ok"
else
  check "_do_up exports GIT_SHA=HEAD inside a git repo" "got '$(tail -n1 "$TMP_ROOT/calls.sha")'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
