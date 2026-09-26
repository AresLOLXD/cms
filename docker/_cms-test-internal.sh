#!/usr/bin/env bash

sudo chown cmsuser:cmsuser ./codecov

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
# 7 files listed below still call gevent.monkey.patch_all(); the rest were
# migrated to asyncio in sub-project 2.4 -- including
# proxyservice_groups_test.py and twophase_gate_consistency_test.py, which
# used to call it but no longer do), so it can't be assigned to either
# group as a whole directory -- list its gevent-touching files
# individually and --ignore just those from the asyncio group.
GEVENT_SERVICE_FILES="cmstestsuite/unit_tests/service/ScoringServiceTest.py cmstestsuite/unit_tests/service/twophase_evaluationservice_test.py cmstestsuite/unit_tests/service/write_results_creates_result_test.py cmstestsuite/unit_tests/service/ProxyServiceTest.py cmstestsuite/unit_tests/service/proxyexecutor_test.py cmstestsuite/unit_tests/service/twophase_reenqueue_test.py cmstestsuite/unit_tests/service/twophase_write_results_test.py"
GEVENT_TEST_PATHS="cmstestsuite/unit_tests/cmscontrib cmstestsuite/unit_tests/cmsranking cmstestsuite/unit_tests/db/rankinggroup_test.py $GEVENT_SERVICE_FILES"

pytest --cov . --cov-report= --junitxml=codecov/junit-gevent.xml -o junit_family=legacy $GEVENT_TEST_PATHS
UNIT_GEVENT=$?

IGNORE_ARGS="--ignore=cmstestsuite/unit_tests/cmscontrib --ignore=cmstestsuite/unit_tests/cmsranking --ignore=cmstestsuite/unit_tests/db/rankinggroup_test.py"
for f in $GEVENT_SERVICE_FILES; do
    IGNORE_ARGS="$IGNORE_ARGS --ignore=$f"
done

pytest --cov . --cov-append --cov-report xml:codecov/unittests.xml \
    --junitxml=codecov/junit-asyncio.xml -o junit_family=legacy $IGNORE_ARGS
UNIT_ASYNCIO=$?

dropdb --host=testdb --username=postgres cmsdbfortesting
createdb --host=testdb --username=postgres cmsdbfortesting
cmsInitDB

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
