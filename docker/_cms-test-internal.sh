#!/usr/bin/env bash

# codecov/ is bind-mounted from the host, which is expected to make it
# writable (see main.yml and cms-test.sh). This chown only helps where sudo
# allows it, and the image's sudoers permits nothing but isolate, so it must
# neither prompt for a password (-n) nor abort the run when it fails.
sudo -n chown cmsuser:cmsuser ./codecov 2>/dev/null || true

dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting
createdb --host=testdb --username=postgres cmsdbfortesting
cmsInitDB

# Split into two pytest processes. Several cmscontrib/ modules call
# gevent.monkey.patch_all() at import time; any test file that imports one
# of them (directly, or transitively like db/rankinggroup_test.py ->
# cmscontrib.DumpImporter) monkey-patches threading/socket for the whole
# process. Any asyncio-based test collected afterward in that same process
# then hangs forever: the asyncio event-loop thread ends up trapped inside
# gevent's own hub instead of running asyncio. Keeping the two kinds of
# tests in separate processes avoids the collision entirely.
#
# cmstestsuite/unit_tests/service/ contains a mix of both kinds itself (the
# 2 files listed below still call gevent.monkey.patch_all(); the rest were
# migrated to asyncio in sub-project 2.4 -- including ScoringServiceTest.py,
# write_results_creates_result_test.py, twophase_write_results_test.py,
# ProxyServiceTest.py and proxyexecutor_test.py, which used to call it but
# no longer do), so it can't be assigned to either group as a whole
# directory -- list its gevent-touching files individually and --ignore
# just those from the asyncio group.
GEVENT_SERVICE_FILES="cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py"
GEVENT_TEST_PATHS="cmstestsuite/unit_tests/cmscontrib cmstestsuite/unit_tests/cmsranking cmstestsuite/unit_tests/db/rankinggroup_test.py $GEVENT_SERVICE_FILES"

# A hung test would otherwise block the job until the CI runner's own
# limit. Abort each group after UNIT_TIMEOUT seconds instead: SIGABRT
# makes pytest's faulthandler dump every thread's traceback first.
# --foreground keeps pytest in the terminal's process group, so Ctrl-C
# still reaches it when the script runs interactively with a TTY.
#
# The functional tests get FUNC_TIMEOUT seconds and SIGINT instead: the
# harness then shuts its services down rather than leaving them running
# (it is SIGKILLed 60 s later if it still hasn't exited).
# Override either limit with `docker compose run -e UNIT_TIMEOUT=...`.
# ulimit -c 0 keeps the SIGABRTs from leaving core dumps behind.
UNIT_TIMEOUT=${UNIT_TIMEOUT:-1200}
FUNC_TIMEOUT=${FUNC_TIMEOUT:-1800}
ulimit -c 0

timeout --foreground --signal=ABRT $UNIT_TIMEOUT \
    pytest --cov . --cov-report= --junitxml=codecov/junit-gevent.xml -o junit_family=legacy $GEVENT_TEST_PATHS
UNIT_GEVENT=$?

IGNORE_ARGS="--ignore=cmstestsuite/unit_tests/cmscontrib --ignore=cmstestsuite/unit_tests/cmsranking --ignore=cmstestsuite/unit_tests/db/rankinggroup_test.py"
for f in $GEVENT_SERVICE_FILES; do
    IGNORE_ARGS="$IGNORE_ARGS --ignore=$f"
done

timeout --foreground --signal=ABRT $UNIT_TIMEOUT \
    pytest --cov . --cov-append --cov-report xml:codecov/unittests.xml \
    --junitxml=codecov/junit-asyncio.xml -o junit_family=legacy $IGNORE_ARGS
UNIT_ASYNCIO=$?

dropdb --host=testdb --username=postgres cmsdbfortesting
createdb --host=testdb --username=postgres cmsdbfortesting
cmsInitDB

timeout --foreground --signal=INT --kill-after=60 "$FUNC_TIMEOUT" \
    cmsRunFunctionalTests -v --coverage codecov/functionaltests.xml
FUNC=$?

# This check is needed because otherwise failing unit tests aren't reported in
# the CI as long as the functional tests are passing. Ideally we should get rid
# of `cmsRunFunctionalTests` and make those tests work with pytest so they can
# be auto-discovered and run in a single command.
if [ $UNIT_GEVENT -ne 0 ] || [ $UNIT_ASYNCIO -ne 0 ] || [ $FUNC -ne 0 ]
then
    exit 1
else
    exit 0
fi
