#!/usr/bin/env bash
# Run one contest-shaped load test against the local cmsload-* stack.
#
#   ./run.sh <run-name> <users-a> <users-b> <login-window-s> <contest-s> \
#            <end-burst-s> <steady-rate-per-min> [end-burst-factor]
#
# e.g. smoke: ./run.sh smoke 14 6 60 480 120 10
#      full:  ./run.sh full1 175 75 150 1500 300 45
#
# Brings the stack up (fresh DB), creates the contests, starts the monitor
# and a docker-stats sampler, runs the driver, then exports the DB and logs
# into out/<run-name>/. Tear down with ./teardown.sh.
set -euo pipefail

RUN=$1 UA=$2 UB=$3 LOGIN=$4 CONTEST=$5 ENDB=$6 RATE=$7 EBF=${8:-1.0}
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ=cmsload-run
OUT="$HERE/out/$RUN"
DC=(docker --context default)
COMPOSE=("${DC[@]}" compose -p "$PROJ" -f "$HERE/compose.yml" --env-file "$HERE/.env.load")

running=$("${DC[@]}" ps --format '{{.Names}}' | grep -E '^(cmsci-|mc2-e2e-)|stress' | grep -v "^$PROJ" || true)
if [[ -n "$running" ]]; then
  echo "Another CI/stress stack is running, refusing to start: $running" >&2
  exit 1
fi

mkdir -p "$OUT"; chmod 777 "$OUT"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d --wait db ranking driver
"${COMPOSE[@]}" up -d cms
echo "Waiting for the CMS services..."
SUP=("${COMPOSE[@]}" exec -T cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf status)
for _ in $(seq 90); do
  st=$("${SUP[@]}" 2>/dev/null || true)
  if [[ -n "$st" ]] && ! grep -qv RUNNING <<<"$st"; then break; fi
  sleep 2
done
sleep 10
"${COMPOSE[@]}" exec -T cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf status | tee "$OUT/supervisor_start.txt"

# Contest start = now + hashing + login window + margin.
"${COMPOSE[@]}" exec -T cms python3 /loadtest/setup_contest.py \
  --start-in $((LOGIN + 45)) --duration "$CONTEST" --users-a "$UA" --users-b "$UB" \
  --out "/loadtest/out/$RUN/users.json"
# Tell ProxyService about the new contests (AWS does this after edits).
"${COMPOSE[@]}" exec -T cms python3 -c "
import sys; sys.path.insert(0, '/loadtest'); import monitor
print(monitor.rpc(('localhost', 28600), 'reinitialize'))"

"${COMPOSE[@]}" exec -d cms python3 /loadtest/monitor.py --out "/loadtest/out/$RUN/monitor.jsonl"
( while true; do
    "${DC[@]}" stats --no-stream --format '{{json .}}' | sed "s/^/$(date +%s) /" >> "$OUT/docker_stats.log"
    sleep 5
  done ) &
STATS_PID=$!
trap 'kill $STATS_PID 2>/dev/null || true' EXIT

"${COMPOSE[@]}" exec -T driver python3 /loadtest/driver.py \
  --users-file "/loadtest/out/$RUN/users.json" \
  --cws http://cms:8888 http://cms:8889 --rws http://ranking:8890 \
  --out "/loadtest/out/$RUN" --login-window "$LOGIN" --end-burst "$ENDB" \
  --steady-rate "$RATE" --end-burst-factor "$EBF" 2>&1 | tee "$OUT/driver_stdout.log"

sleep 15
"${COMPOSE[@]}" exec -T cms pkill -f /loadtest/monitor.py || true
"${COMPOSE[@]}" exec -T cms python3 /loadtest/db_export.py > "$OUT/db_export.json"
rm -rf "$OUT/cmslog"; mkdir -p "$OUT/cmslog"
"${COMPOSE[@]}" cp cms:/home/cmsuser/cms/log/. "$OUT/cmslog/"
"${COMPOSE[@]}" logs --no-color cms > "$OUT/cms_stdout.log" 2>&1
"${COMPOSE[@]}" logs --no-color db > "$OUT/db.log" 2>&1
"${COMPOSE[@]}" logs --no-color ranking > "$OUT/ranking.log" 2>&1
"${COMPOSE[@]}" exec -T cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf status > "$OUT/supervisor_end.txt" || true
echo "Run $RUN done: $OUT"
