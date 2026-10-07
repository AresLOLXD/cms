#!/usr/bin/env bash

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Start every CMS service of the load-test stack (except ProxyService,
# which run.sh starts once the contests exist) and wait forever. Used by
# both targets so that they run the same processes the same way. A
# service that dies stays dead: a crash under load is a finding, so every
# exit is logged to start.log (and to the container log) with the
# service name, the exit status and the UTC time.
set -euo pipefail
: "${CMS_CONFIG:?CMS_CONFIG must point to the rendered cms.toml}"
WORKERS=${LOAD_WORKER_COUNT:?}
CWS=${LOAD_CWS_COUNT:?}
RUN_DIR=/loadtest/run
LOG=$RUN_DIR/start.log

if [[ ! -d $RUN_DIR || ! -w $RUN_DIR ]]; then
  echo "start.sh: $RUN_DIR is missing or not writable. Mount the load-test" \
    "directory at /loadtest and create run/ with mode 777 (run.sh does)." >&2
  exit 1
fi
# "wait -n -p" needs bash 5.1; both images are ubuntu:noble (5.2) or
# debian:bookworm (5.2).
if ((BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1))); then
  echo "start.sh: bash $BASH_VERSION is too old, 5.1 or newer is required" >&2
  exit 1
fi

declare -A SERVICE_OF_PID
start() {
  "$@" >>"$LOG" 2>&1 &
  SERVICE_OF_PID[$!]="$*"
  echo "$(date -u +%FT%TZ) started $* (pid $!)" >>"$LOG"
}

start cmsLogService 0
sleep 2
start cmsScoringService 0 -c ALL
start cmsEvaluationService 0 -c ALL
for ((i = 0; i < WORKERS; i++)); do start cmsWorker "$i" -c ALL; done
for ((i = 0; i < CWS; i++)); do start cmsContestWebServer "$i" -c ALL; done
start cmsAdminWebServer 0 -c ALL

while ((${#SERVICE_OF_PID[@]} > 0)); do
  finished_pid=
  status=0
  wait -n -p finished_pid || status=$?
  [[ -n $finished_pid ]] || break
  message="$(date -u +%FT%TZ) EXITED ${SERVICE_OF_PID[$finished_pid]} (pid $finished_pid) status $status"
  if ((status > 128)); then
    message+=" (signal $((status - 128)))"
  fi
  echo "$message" | tee -a "$LOG" >&2
  unset "SERVICE_OF_PID[$finished_pid]"
done
