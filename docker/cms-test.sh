#!/usr/bin/env bash
set -x

GIT_BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD | tr A-Z a-z)

# The container's cmsuser (uid 2000) writes the test reports here but can't
# chown the directory itself (its sudo is limited to isolate). Stop if that
# fails: the tests would run for a long while and then die on the report.
mkdir -p codecov && chmod 777 codecov || {
    echo "codecov/ is not writable; remove it (it may be owned by root) and retry." >&2
    exit 1
}

docker compose -p cms-$GIT_BRANCH_NAME -f docker/docker-compose.test.yml run --build --rm testcms
