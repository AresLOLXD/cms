#!/usr/bin/env bash
# Start every CMS service of the load-test stack (except ProxyService,
# which run.sh starts once the contests exist) and wait forever. Used by
# both targets so that they run the same processes the same way. A
# service that dies stays dead: a crash under load is a finding.
set -euo pipefail
: "${CMS_CONFIG:?CMS_CONFIG must point to the rendered cms.toml}"
WORKERS=${LOAD_WORKER_COUNT:?}
CWS=${LOAD_CWS_COUNT:?}
LOG=/loadtest/run/start.log
start() { "$@" >>"$LOG" 2>&1 & echo "started $* (pid $!)" >>"$LOG"; }

start cmsLogService 0
sleep 2
start cmsScoringService 0 -c ALL
start cmsEvaluationService 0 -c ALL
for ((i = 0; i < WORKERS; i++)); do start cmsWorker "$i" -c ALL; done
for ((i = 0; i < CWS; i++)); do start cmsContestWebServer "$i" -c ALL; done
start cmsAdminWebServer 0 -c ALL
wait
