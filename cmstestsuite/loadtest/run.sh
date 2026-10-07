#!/usr/bin/env bash
# Run one contest-shaped load test of one target against a fresh local
# cmsload-<target> stack (the image comes from ./build.sh).
#
#   ./run.sh --target fork|upstream --profile portable|full --name NAME \
#            [--users-a N] [--users-b N] [--login-window S] [--contest S] \
#            [--end-burst S] [--rate R] [--end-burst-factor F] \
#            [--workers N] [--cws N] [--request-time-header NAME]
#
# The defaults are the 2026-09-30 full1 values: users-a 175, users-b 75,
# login-window 150, contest 1500, end-burst 300, rate 45 (submissions per
# minute), end-burst-factor 1.0, workers 8, cws 2, no request time header.
# --request-time-header NAME simulates a front proxy that stamps the arrival
# time of each submit in the header NAME (the driver sends "NAME: t=<ms>" and
# the fork target is configured to read it); the upstream target ignores the
# key with a warning.
#   smoke: ./run.sh --target fork --profile portable --name smoke \
#              --users-a 14 --users-b 6 --login-window 60 --contest 480 \
#              --end-burst 120 --rate 10
#
# Brings the stack up (fresh DB), creates the contests, starts ProxyService,
# the monitor and a docker-stats sampler, runs the driver, then collects the
# DB export and the logs into out/<name>/ and runs analyze.py. The stack is
# left running; tear it down with ./teardown.sh <target>.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

TARGET='' PROFILE='' NAME='' REQUEST_TIME_HEADER=''
UA=175 UB=75 LOGIN=150 CONTEST=1500 ENDB=300 RATE=45 EBF=1.0 WORKERS=8 CWS=2

usage() {
  sed -n '2,/^# left running/p' "$0" | sed 's/^# \{0,1\}//' >&2
}

while (($#)); do
  case $1 in
    --target) TARGET=${2:?--target needs a value}; shift 2 ;;
    --profile) PROFILE=${2:?--profile needs a value}; shift 2 ;;
    --name) NAME=${2:?--name needs a value}; shift 2 ;;
    --users-a) UA=${2:?--users-a needs a value}; shift 2 ;;
    --users-b) UB=${2:?--users-b needs a value}; shift 2 ;;
    --login-window) LOGIN=${2:?--login-window needs a value}; shift 2 ;;
    --contest) CONTEST=${2:?--contest needs a value}; shift 2 ;;
    --end-burst) ENDB=${2:?--end-burst needs a value}; shift 2 ;;
    --rate) RATE=${2:?--rate needs a value}; shift 2 ;;
    --end-burst-factor) EBF=${2:?--end-burst-factor needs a value}; shift 2 ;;
    --workers) WORKERS=${2:?--workers needs a value}; shift 2 ;;
    --cws) CWS=${2:?--cws needs a value}; shift 2 ;;
    --request-time-header) REQUEST_TIME_HEADER=${2:?--request-time-header needs a value}; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ $TARGET == fork || $TARGET == upstream ]] ||
  { echo "--target must be fork or upstream" >&2; exit 2; }
[[ $PROFILE == portable || $PROFILE == full ]] ||
  { echo "--profile must be portable or full" >&2; exit 2; }
[[ $NAME =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
  { echo "--name is required: letters, digits, dot, dash and underscore, starting with a letter or digit" >&2; exit 2; }
[[ -z $REQUEST_TIME_HEADER || $REQUEST_TIME_HEADER =~ ^[A-Za-z0-9-]+$ ]] ||
  { echo "--request-time-header must be a header name: letters, digits and dashes" >&2; exit 2; }
if [[ $PROFILE == full && $TARGET == upstream ]]; then
  echo "The full profile uses fork-only features (dependencies, two-phase, ranking groups); run it on the fork target." >&2
  exit 2
fi

PROJ=cmsload-$TARGET
OUT="$HERE/out/$NAME"
RUN_DIR="$HERE/run"

# Docker always goes through the rootful engine. printf %q keeps every
# argument intact through the shell that "sg" starts.
dc() { sg docker -c "$(printf '%q ' docker --context default "$@")"; }
compose() {
  dc compose -p "$PROJ" -f "$HERE/compose.yml" --env-file "$HERE/.env.load" "$@"
}

# One stack at a time: isolate cgroups are shared with the host, and another
# CI or stress stack would skew the numbers. This target's own stack is
# fine, it is replaced below.
running=$(dc ps --format '{{.Names}}' |
  grep -E '^(cmsload-|cmsci-|mc2-e2e-)|stress' | grep -v "^$PROJ-" || true)
if [[ -n $running ]]; then
  echo "Another load-test, CI or stress stack is running, refusing to start: $running" >&2
  exit 1
fi
if ! dc image inspect "cmsload-$TARGET:latest" >/dev/null 2>&1; then
  echo "Image cmsload-$TARGET:latest not found; build it with ./build.sh $TARGET <git-ref>" >&2
  exit 1
fi
if [[ -n $(ls -A "$OUT" 2>/dev/null) ]]; then
  echo "$OUT already has files; use another --name or remove it" >&2
  exit 2
fi

# Throwaway secrets for this run.
token() { python3 -c 'import secrets; print(secrets.token_hex(16))'; }
DB_PASSWORD=$(token)
RWS_PASSWORD=$(token)
SECRET_KEY=$(token)
(
  umask 077
  cat >"$HERE/.env.load" <<EOF
LOAD_TARGET=$TARGET
LOAD_DB_PASSWORD=$DB_PASSWORD
LOAD_RWS_PASSWORD=$RWS_PASSWORD
LOAD_SECRET_KEY=$SECRET_KEY
LOAD_WORKER_COUNT=$WORKERS
LOAD_CWS_COUNT=$CWS
EOF
)
chmod 600 "$HERE/.env.load"

# The containers run as uid 2000 and write into the mounted directory.
rm -rf "$RUN_DIR"
mkdir "$RUN_DIR"
chmod 777 "$RUN_DIR"
TWO_PHASE=false
[[ $PROFILE == full ]] && TWO_PHASE=true
# Handed to both render_config.py and the driver, only when it is set.
REQUEST_TIME_ARGS=()
[[ -n $REQUEST_TIME_HEADER ]] &&
  REQUEST_TIME_ARGS=(--request-time-header "$REQUEST_TIME_HEADER")
python3 "$HERE/render_config.py" --out-dir "$RUN_DIR" \
  --db-url "postgresql+psycopg2://cms:$DB_PASSWORD@db:5432/cmsdb" \
  --secret-key "$SECRET_KEY" --rws-password "$RWS_PASSWORD" \
  --workers "$WORKERS" --cws "$CWS" --two-phase "$TWO_PHASE" \
  "${REQUEST_TIME_ARGS[@]}"

# Process census: how many cms<Service> processes run in the cms container
# against what start.sh and this script started. A service that dies stays
# dead, and the census (and start.log) is how that shows up. It reads /proc
# because the images may not have ps.
CENSUS_SERVICES=(LogService:1 ScoringService:1 EvaluationService:1
  "Worker:$WORKERS" "ContestWebServer:$CWS" AdminWebServer:1 ProxyService:1)
# shellcheck disable=SC2016  # the command runs in the container's shell
CENSUS_COMMAND='for p in /proc/[0-9]*; do n=0; while IFS= read -r -d "" arg && ((n++ < 2)); do case "${arg##*/}" in cms[A-Z]*) echo "${arg##*/}"; break ;; esac; done 2>/dev/null <"$p/cmdline"; done | sort | uniq -c'
CENSUS_WARNINGS=()

# take_census <label> <file>: write the found and expected count of every
# service to file, and warn about the ones that are short.
take_census() {
  local label=$1 file=$2 raw entry name want found short=0
  raw=$(compose exec -T cms bash -c "$CENSUS_COMMAND" 2>/dev/null) || true
  {
    echo "# process census at the $label of the run, $(date -u +%FT%TZ)"
    for entry in "${CENSUS_SERVICES[@]}"; do
      name=cms${entry%%:*}
      want=${entry##*:}
      found=$(awk -v name="$name" '$2 == name { print $1 }' <<<"$raw")
      found=${found:-0}
      if ((found < want)); then
        short=1
        echo "$name expected=$want found=$found SHORT"
      else
        echo "$name expected=$want found=$found"
      fi
    done
  } >"$file"
  if ((short)); then
    CENSUS_WARNINGS+=("WARNING: CMS processes are missing at the $label of the run, see $file and $OUT/start.log")
  fi
}

COLLECTED=
STATS_PID=
# collect: gather everything the analysis needs from the stack. It runs once,
# from the main flow or, if a step failed, from the EXIT trap, and it never
# stops on an error so that as much as possible is saved.
collect() {
  [[ -z $COLLECTED ]] || return 0
  COLLECTED=1
  local had_errexit=0
  [[ $- == *e* ]] && had_errexit=1
  set +e
  echo "Collecting the logs into $OUT"
  [[ -z $STATS_PID ]] || kill "$STATS_PID" 2>/dev/null
  take_census end "$OUT/processes_end.txt"
  # shellcheck disable=SC2016  # expanded in the container
  compose exec -T cms bash -c 'kill "$(cat /loadtest/run/monitor.pid)"' >/dev/null 2>&1
  if compose exec -T cms python3 /loadtest/db_export.py >"$OUT/db_export.json.tmp"; then
    mv "$OUT/db_export.json.tmp" "$OUT/db_export.json"
  else
    rm -f "$OUT/db_export.json.tmp"
    echo "db_export.py failed, there is no db_export.json" >&2
  fi
  rm -rf "$OUT/cmslog"
  mkdir -p "$OUT/cmslog"
  compose cp cms:/home/cmsuser/cms/log/. "$OUT/cmslog/"
  cp "$RUN_DIR/start.log" "$OUT/start.log"
  compose logs --no-color cms >"$OUT/cms_stdout.log" 2>&1
  compose logs --no-color db >"$OUT/db.log" 2>&1
  compose logs --no-color ranking >"$OUT/ranking.log" 2>&1
  compose logs --no-color db-init >"$OUT/db_init.log" 2>&1
  # The config without the secret key, the database URL and the ranking URL
  # (it holds the ranking password).
  grep -v -e secret_key -e '^url' -e '^rankings' "$RUN_DIR/cms.toml" >"$OUT/cms.toml"
  cp "$HERE/out/images/$TARGET.txt" "$OUT/image.txt"
  if ((had_errexit)); then set -e; fi
}
# shellcheck disable=SC2329  # called from the EXIT trap
print_census_warnings() {
  local warning
  for warning in "${CENSUS_WARNINGS[@]}"; do echo "$warning"; done
}

mkdir -p "$OUT"
chmod 777 "$OUT"
trap 'collect; print_census_warnings' EXIT
# Ctrl-C, or kill -INT/-TERM of this script: stop the driver, whose
# requests would go on in its container, and exit; the EXIT trap collects.
# shellcheck disable=SC2329  # called from the INT and TERM traps
on_signal() {
  compose kill driver >/dev/null 2>&1 || true
  exit "$1"
}
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

compose down -v --remove-orphans >/dev/null 2>&1 || true
compose up -d --wait db
compose build driver
compose up -d ranking driver cms

echo "Waiting for the CMS services..."
CWS_URLS=()
for ((i = 0; i < CWS; i++)); do CWS_URLS+=("http://cms:$((8888 + i))"); done
HTTP_URLS=("${CWS_URLS[@]}" http://cms:8898 http://ranking:8890)
RPC_PORTS=(25000 28500)
for ((i = 0; i < WORKERS; i++)); do RPC_PORTS+=($((26000 + i))); done

# Any HTTP status counts as up, 404 and 302 included; only a connection
# error is not ready. Run in the driver container, which is on the network.
probe_http() {
  compose exec -T driver python3 - "$@" <<'PY'
import sys
import urllib.error
import urllib.request

bad = []
for url in sys.argv[1:]:
    try:
        urllib.request.urlopen(url, timeout=3)
    except urllib.error.HTTPError:
        pass
    except Exception as exc:
        bad.append("%s (%s)" % (url, exc))
print("; ".join(bad))
sys.exit(1 if bad else 0)
PY
}
# An RPC echo to every port, from inside the cms container.
probe_rpc() {
  compose exec -T cms python3 - "$@" <<'PY'
import sys

sys.path.insert(0, "/loadtest")
import monitor

bad = []
for port in map(int, sys.argv[1:]):
    try:
        _, _, error = monitor.rpc(("localhost", port), "echo",
                                  {"string": "ok"}, timeout=3)
        if error:
            bad.append("%d (%s)" % (port, error))
    except Exception as exc:
        bad.append("%d (%r)" % (port, exc))
print("; ".join(bad))
sys.exit(1 if bad else 0)
PY
}
deadline=$((SECONDS + 180))
while true; do
  not_ready=
  http_bad=$(probe_http "${HTTP_URLS[@]}" 2>&1) || not_ready+=" http: $http_bad"
  rpc_bad=$(probe_rpc "${RPC_PORTS[@]}" 2>&1) || not_ready+=" rpc: $rpc_bad"
  [[ -n $not_ready ]] || break
  if ((SECONDS >= deadline)); then
    echo "The CMS services are not ready after 180 s. Not ready:$not_ready" >&2
    echo "--- $RUN_DIR/start.log" >&2
    cat "$RUN_DIR/start.log" >&2 || true
    exit 1
  fi
  sleep 3
done

# Contest start = now + hashing + login window + margin.
compose exec -T cms python3 /loadtest/setup_contest.py --profile "$PROFILE" \
  --start-in $((LOGIN + 45)) --duration "$CONTEST" \
  --users-a "$UA" --users-b "$UB" --out "/loadtest/out/$NAME/users.json"
# Record the target in users.json, which analyze.py reads. The file belongs
# to the container's user, so it is replaced rather than edited.
python3 -c '
import json, os, sys
path, target = sys.argv[1:]
with open(path) as f:
    data = json.load(f)
data["target"] = target
with open(path + ".tmp", "w") as f:
    json.dump(data, f)
os.replace(path + ".tmp", path)
' "$OUT/users.json" "$TARGET"

# ProxyService starts now that the contests exist. The portable profile has
# one ranked contest; the full one serves every contest and ranking group
# ("-c ALL" is multi-contest mode; without -c ProxyService would ask for a
# contest, or exit when it has no TTY).
if [[ $PROFILE == portable ]]; then
  loada_id=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["contest_ids"]["loada"])' "$OUT/users.json")
  proxy_args="0 -c $loada_id"
else
  proxy_args="0 -c ALL"
fi
# The status goes into a variable first: the date command substitution
# would reset $?.
compose exec -d cms bash -c "cmsProxyService $proxy_args >>/loadtest/run/start.log 2>&1; rc=\$?; echo \"\$(date -u +%FT%TZ) EXITED cmsProxyService $proxy_args (run.sh) status \$rc\" >>/loadtest/run/start.log"
proxy_deadline=$((SECONDS + 60))
until probe_rpc 28600 >/dev/null 2>&1; do
  if ((SECONDS >= proxy_deadline)); then
    echo "ProxyService does not answer on 28600 after 60 s" >&2
    echo "--- $RUN_DIR/start.log" >&2
    cat "$RUN_DIR/start.log" >&2 || true
    exit 1
  fi
  sleep 2
done
# Every service is up now: the census at the start of the run.
take_census start "$OUT/processes_start.txt"
print_census_warnings

compose exec -d cms bash -c "echo \$\$ >/loadtest/run/monitor.pid; exec python3 /loadtest/monitor.py --out /loadtest/out/$NAME/monitor.jsonl --cws-count $CWS"
(
  while true; do
    dc stats --no-stream --format '{{json .}}' | sed "s/^/$(date +%s) /" >>"$OUT/docker_stats.log" || true
    sleep 5
  done
) &
STATS_PID=$!

# The driver runs in the background so that a signal to this script runs
# its trap at once: bash defers traps until a foreground command ends.
driver_rc=0
compose exec -T driver python3 /loadtest/driver.py \
  --users-file "/loadtest/out/$NAME/users.json" \
  --cws "${CWS_URLS[@]}" --rws http://ranking:8890 \
  --out "/loadtest/out/$NAME" --login-window "$LOGIN" --end-burst "$ENDB" \
  --steady-rate "$RATE" --end-burst-factor "$EBF" \
  "${REQUEST_TIME_ARGS[@]}" 2>&1 |
  tee "$OUT/driver_stdout.log" &
wait $! || driver_rc=$?

# analyze.py needs the DB export and the logs, so collect before it.
sleep 15
collect
python3 "$HERE/analyze.py" "$OUT" --project-prefix "$PROJ"
echo "Run $NAME done: $OUT"
exit "$driver_rc"
