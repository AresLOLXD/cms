# Ranking Dockerfile — Design Spec

**Date:** 2026-06-11
**Status:** Approved

## Problem

The `ranking` Docker service currently uses the same image as `cms`. That image
includes language compilers (Java, Rust, GHC, Pascal, Mono, PyPy, PHP), Node.js,
Karel tools, CMS-Loader, the isolate sandbox, PostgreSQL client, and supervisor.
`cmsRankingWebServer` uses none of these — it is a pure Python web server that
receives score pushes over HTTP and serves the scoreboard UI.

Goals: reduce image size (~2 GB → ~400–500 MB) and reduce the surface area of
what runs inside the ranking container.

## Solution

Add `docker/Dockerfile.ranking` — a dedicated, minimal Dockerfile for the ranking
service. The existing `docker/Dockerfile` is unchanged.

## `Dockerfile.ranking` contents

```
ARG BASE_IMAGE=ubuntu:noble
FROM ${BASE_IMAGE}

apt packages:
  python3 python3-pip python3-venv python3-dev
  build-essential libpq-dev libffi-dev libyaml-dev

cmsuser (uid 2000) — no isolate group, no sudo

pip install: CMS Python package (no dev dependencies)
  uses the same install.py + constraints.txt as the main image

COPY: docker/generate_config.py → /home/cmsuser/generate_config.py
COPY: docker/entrypoint.sh      → /home/cmsuser/entrypoint.sh

ENTRYPOINT ["/home/cmsuser/entrypoint.sh"]
CMD        ["cmsRankingWebServer", "0"]
```

`build-essential`, `libpq-dev`, `libffi-dev`, and `libyaml-dev` are needed because
`pip install` compiles C extensions for packages like `psycopg2`, `cffi`, and
`PyYAML` that are transitive dependencies of the cms package.

No multi-stage build is needed: there is nothing to compile ahead of time.

## Changes to `docker-compose.prod.yml`

```yaml
ranking:
  build:
    context: ..
    dockerfile: docker/Dockerfile.ranking
    # CMS_LOADER_VERSION and CMS_LOADER_BASE_PATH removed — not applicable
  env_file:
    - path: ../.env
      required: false
  environment:
    CMS_RANKING_ONLY: "true"
  command: ["cmsRankingWebServer", "0"]
  ports:
    - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
  restart: unless-stopped
  logging:
    driver: "json-file"
    options:
      max-size: "${CMS_LOG_MAX_SIZE:-500m}"
      max-file: "${CMS_LOG_MAX_FILES:-10}"
```

## What is explicitly excluded

| Excluded | Reason |
|----------|--------|
| Node.js / npm / pnpm | No JS runtime needed |
| rekarel / karel binary | Karel grading runs in the Worker, not ranking |
| CMS-Loader | Task import tool, unrelated to scoreboard |
| Language compilers (Java, Rust, GHC, etc.) | Grading is done by the Worker |
| isolate sandbox | No untrusted code execution |
| supervisor | Single process — no process manager needed |
| sudo | No privileged operations |
| PostgreSQL client | Ranking has no DB access |
| `privileged: true` / `cgroup: host` | Not needed without isolate |

## Verification

1. `docker compose build ranking` — builds without errors
2. `docker compose up ranking` — scoreboard responds on port 8890
3. `docker images` — ranking image is significantly smaller than cms image
