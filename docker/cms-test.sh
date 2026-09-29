#!/usr/bin/env bash
set -x

GIT_BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD | tr A-Z a-z)

# The container's cmsuser (uid 2000) writes the test reports here but can't
# chown the directory itself (its sudo is limited to isolate).
mkdir -p codecov && chmod 777 codecov

docker compose -p cms-$GIT_BRANCH_NAME -f docker/docker-compose.test.yml run --build --rm testcms
