# CMS-Loader Local Validation — Design Spec

**Date:** 2026-05-18  
**Status:** Approved

## Overview

Interactive local deployment of CMS using podman to validate the CMS-Loader v1.0.4
pinning feature end-to-end: build the image with a pinned tag, start the stack,
create a contest, bulk-load synthetic users via the CMS-Loader UI, and verify the
import succeeded. All temporary artifacts are removed at the end of the session.

## Goals

- Confirm `CMS_LOADER_VERSION=v1.0.4` pins the build to that tag (not `main`).
- Confirm CMS-Loader starts under supervisord and is reachable on port 9995.
- Confirm the CSV bulk-load creates users and participations in the DB.

## Non-Goals

- Worker sandbox correctness (rootless podman, cgroup:host is ignored — acceptable).
- Submission grading.
- Persistent deployment.

## Stack

```
podman compose -p cms-loadertest
               -f docker/docker-compose.prod.yml
               --env-file .env.test
               --profile localdb
```

Services: `db` (postgres:17-alpine), `db-init` (cmsSetupDB), `cms` (supervisord).
CMS-Loader runs inside `cms` on port 9995.

## Temporary Files

| File | Created by | Purpose |
|---|---|---|
| `.env.test` | session | Test environment variables |
| `docker/test-data/users_test.csv` | session | 20 synthetic users |

Both deleted during cleanup.

## Test Flow

| # | Actor | Action |
|---|---|---|
| 1 | Claude | Generate `.env.test` with test values + `CMS_LOADER_VERSION=v1.0.4` |
| 2 | Claude | `podman compose build` |
| 3 | Claude | `podman compose up -d --profile localdb` |
| 4 | Claude | Wait for healthchecks + monitor supervisord log |
| 5 | User | `http://localhost:8889` → login → create contest "Test Contest" |
| 6 | Claude | Generate `users_test.csv` (20 users, contest_id=1) |
| 7 | User | `http://localhost:9995` → login → upload CSV |
| 8 | Claude | Verify users + participations via `podman exec` + psql |
| 9 | Claude | `podman compose down -v` + delete temp files |

## .env.test Variables

```bash
CMS_DB_URL=postgresql+psycopg2://cms:cmstest@db:5432/cmsdb
POSTGRES_USER=cms
POSTGRES_PASSWORD=cmstest
POSTGRES_DB=cmsdb
CMS_SECRET_KEY=<16-byte hex, generated>
CMS_ADMIN_USER=admin
CMS_ADMIN_PASSWORD=admin
CMS_CONTEST_ID=1
CMS_LOADER_VERSION=v1.0.4
CMS_LOADER_SESSION_SECRET=<base64-32, generated>
CMS_LOADER_ADMIN_USER=admin
CMS_LOADER_ADMIN_PASSWORD=admin
CMS_LOADER_PORT=9995
CMS_CWS_HTTP_PORT=8888
CMS_AWS_HTTP_PORT=8889
CMS_RWS_HTTP_PORT=8890
```

## Success Criteria

1. Build log shows `git clone --branch v1.0.4` for CMS-Loader.
2. `supervisorctl status` inside the container shows `cmsloader RUNNING`.
3. `http://localhost:9995` returns a login page.
4. After CSV upload, `SELECT count(*) FROM users` returns 20.
5. `SELECT count(*) FROM participations WHERE contest_id=1` returns 20.
6. After cleanup, `podman volume ls | grep loadertest` returns nothing.
