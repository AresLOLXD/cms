#!/usr/bin/env bash
# Tests the sed logic used by contest.sh to update CMS_CONTEST_ID in .env
set -euo pipefail

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

echo "=== contest.sh .env update tests ==="

TMPENV=$(mktemp)

# Test: update existing CMS_CONTEST_ID
echo "CMS_CONTEST_ID=1" > "$TMPENV"
sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=5/" "$TMPENV"
result=$(grep '^CMS_CONTEST_ID=' "$TMPENV" | cut -d= -f2)
if [[ "$result" == "5" ]]; then
  check "sed updates existing CMS_CONTEST_ID" "ok"
else
  check "sed updates existing CMS_CONTEST_ID" "got '$result'"
fi

# Test: append when CMS_CONTEST_ID is absent
echo "OTHER_VAR=foo" > "$TMPENV"
if ! grep -qE '^CMS_CONTEST_ID=' "$TMPENV"; then
  echo "CMS_CONTEST_ID=3" >> "$TMPENV"
fi
result=$(grep '^CMS_CONTEST_ID=' "$TMPENV" | cut -d= -f2)
if [[ "$result" == "3" ]]; then
  check "appends CMS_CONTEST_ID when not present" "ok"
else
  check "appends CMS_CONTEST_ID when not present" "got '$result'"
fi

# Test: only updates CMS_CONTEST_ID, leaves other vars intact
printf 'CMS_PROJECT_NAME=cms-test\nCMS_CONTEST_ID=1\nCMS_DB_URL=foo\n' > "$TMPENV"
sed -i "s/^CMS_CONTEST_ID=.*/CMS_CONTEST_ID=7/" "$TMPENV"
project=$(grep '^CMS_PROJECT_NAME=' "$TMPENV" | cut -d= -f2)
db=$(grep '^CMS_DB_URL=' "$TMPENV" | cut -d= -f2)
if [[ "$project" == "cms-test" && "$db" == "foo" ]]; then
  check "other .env vars are not modified" "ok"
else
  check "other .env vars are not modified" "project='$project' db='$db'"
fi

rm -f "$TMPENV"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
