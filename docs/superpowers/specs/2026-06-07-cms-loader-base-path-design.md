# CMS-Loader Base Path Configuration

**Date:** 2026-06-07
**Status:** Approved

## Problem

CMS-Loader v1.1.0 added `VITE_BASE_PATH`, a Vite build-time variable that lets the
frontend bundle know what sub-path it will be served from. Without passing this value
during the Docker build, the frontend always assumes it is served at `/`, making it
impossible to run cms-loader behind a reverse proxy at a sub-path (e.g. `/cms/`).

## Solution

Expose `CMS_LOADER_BASE_PATH` as a Docker build argument (following the existing
`CMS_LOADER_VERSION` pattern) so operators can set it in `.env` and get the correct
frontend bundle without any code-path changes.

`VITE_BASE_PATH` is purely build-time — it is baked into the frontend by Vite and the
Express server needs no runtime changes.

## Changes

### `Dockerfile` — `loader-builder` stage

Add `ARG CMS_LOADER_BASE_PATH=/` and pass it to `pnpm run build` as `VITE_BASE_PATH`:

```dockerfile
ARG CMS_LOADER_VERSION=main
ARG CMS_LOADER_BASE_PATH=/

RUN git clone --branch "${CMS_LOADER_VERSION}" --depth 1 \
        https://github.com/AresLOLXD/CMS-Loader.git /build && \
    cd /build && \
    pnpm install && \
    VITE_BASE_PATH="${CMS_LOADER_BASE_PATH}" pnpm run build && \
    pnpm prune --prod --ignore-scripts
```

### `docker/docker-compose.prod.yml`

Add `CMS_LOADER_BASE_PATH` to the `build.args` block of both `db-init` and `cms`
services (same pattern as `CMS_LOADER_VERSION`):

```yaml
args:
  CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
  CMS_LOADER_BASE_PATH: ${CMS_LOADER_BASE_PATH:-/}
```

### `.env.example`

Add to the `CMS-LOADER` section:

```dotenv
# Sub-path prefix when cms-loader is not served at the domain root (build-time).
# Example: /cms/  — the reverse proxy must forward /cms/* to the loader port.
# Default: / (served at domain root). Changing this requires rebuilding the image.
CMS_LOADER_BASE_PATH=/
```

## Out of scope

- No changes to `generate_config.py` — `VITE_BASE_PATH` is build-time only.
- No changes to `test_generate_config.py` — no new runtime behaviour.
- No changes to the Express router — the server does not need to know the base path.

## Reverse proxy note

When `CMS_LOADER_BASE_PATH=/cms/`, a reverse proxy (e.g. nginx) must strip the
prefix before forwarding to the container port, or pass it intact — either works
because Vite's `base` only affects asset URLs in the built HTML, not server routing.
