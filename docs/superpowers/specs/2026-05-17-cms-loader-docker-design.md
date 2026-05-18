# CMS-Loader Docker Integration — Design Spec

**Date:** 2026-05-17  
**Status:** Approved

## Overview

Integrate [CMS-Loader](https://github.com/AresLOLXD/CMS-Loader) into the existing CMS production Docker image. CMS-Loader is a Node.js/TypeScript web application (Express, port 9995) that provides a browser UI for bulk-loading users and participations via CSV. It calls `cmsAddUser` and `cmsAddParticipation` directly — both already in PATH inside the CMS container.

## Decisions

- **Deployment:** Same container as CMS, managed by supervisord alongside existing services.
- **Source:** `git clone` with a pinned tag/branch during Dockerfile build (consistent with the existing `cms_rekarel` install pattern).
- **Build strategy:** New `loader-builder` Dockerfile stage (mirrors `rekarel-builder`). Only compiled artifacts reach the final image — TypeScript compiler and pnpm are discarded.
- **Port:** 9995, exposed via `.env` variable `CMS_LOADER_PORT`, added to `docker-compose.prod.yml`.
- **Credentials:** Passed via `.env` with `CMS_LOADER_` prefix, mapped to CMS-Loader's expected names inside the supervisord program block.
- **Opt-in:** CMS-Loader is only started if all three required credentials are set (`CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER`, `CMS_LOADER_ADMIN_PASSWORD`). Missing vars → service silently skipped, no container error.

## Files Changed

| File | Change |
|---|---|
| `Dockerfile` | New `loader-builder` stage + 3 `COPY` directives to runtime stage |
| `docker/generate_config.py` | Adds `[program:cmsloader]` block to generated supervisord.conf when credentials are present |
| `.env.example` | New `CMS-LOADER` section with 4 variables |
| `docker/docker-compose.prod.yml` | Port `${CMS_LOADER_PORT:-9995}` + build arg `CMS_LOADER_VERSION` |

## Dockerfile — loader-builder stage

Inserted between `rekarel-builder` and the runtime stage:

```dockerfile
# ─── Stage 2: Build CMS-Loader (Node.js/TypeScript) ──────────────────────────
FROM ${BASE_IMAGE} AS loader-builder

ARG CMS_LOADER_VERSION=main

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y curl ca-certificates git
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
EOF

RUN npm install -g pnpm

RUN git clone --branch "${CMS_LOADER_VERSION}" --depth 1 \
        https://github.com/AresLOLXD/CMS-Loader.git /build && \
    cd /build && \
    pnpm install && \
    pnpm run build && \
    pnpm prune --prod
```

In the runtime stage, after the existing `COPY --from=rekarel-builder` directives:

```dockerfile
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/dist         /home/cmsuser/cms-loader/dist
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/node_modules /home/cmsuser/cms-loader/node_modules
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/package.json /home/cmsuser/cms-loader/package.json
```

## generate_config.py — supervisord block

Added at the end of `generate_supervisord_conf()`, before `return`:

```python
loader_secret = os.environ.get("CMS_LOADER_SESSION_SECRET", "")
loader_user   = os.environ.get("CMS_LOADER_ADMIN_USER", "")
loader_pass   = os.environ.get("CMS_LOADER_ADMIN_PASSWORD", "")
loader_port   = _get_int("CMS_LOADER_PORT", 9995)

if loader_secret and loader_user and loader_pass:
    env_str = (
        f'SESSION_SECRET="%(ENV_CMS_LOADER_SESSION_SECRET)s",'
        f'ADMIN_USER="%(ENV_CMS_LOADER_ADMIN_USER)s",'
        f'ADMIN_PASSWORD="%(ENV_CMS_LOADER_ADMIN_PASSWORD)s",'
        f'PORT="{loader_port}",'
        f'NODE_ENV="production"'
    )
    blocks.append(
        f"[program:cmsloader]\n"
        f"priority=70\n"
        f"directory=/home/cmsuser/cms-loader\n"
        f"command=node dist/index.js\n"
        f"environment={env_str}\n"
        "autostart=true\nautorestart=true\n"
        "stdout_logfile=/dev/stdout\nstdout_logfile_maxbytes=0\n"
        "stderr_logfile=/dev/stderr\nstderr_logfile_maxbytes=0\n"
    )
```

## .env.example — new section

```bash
# -----------------------------------------------------------
# CMS-LOADER (optional — bulk user/participation importer)
# -----------------------------------------------------------

# Random 32+ character string to sign session cookies.
# Generate one with: openssl rand -base64 32
CMS_LOADER_SESSION_SECRET=

# Administrator credentials for the CMS-Loader web UI.
CMS_LOADER_ADMIN_USER=
CMS_LOADER_ADMIN_PASSWORD=

# Port where CMS-Loader listens (default: 9995).
CMS_LOADER_PORT=9995
```

## docker-compose.prod.yml — changes

```yaml
cms:
  build:
    context: ..
    args:
      CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
  ports:
    - "${CMS_CWS_HTTP_PORT:-8888}:${CMS_CWS_HTTP_PORT:-8888}"
    - "${CMS_AWS_HTTP_PORT:-8889}:${CMS_AWS_HTTP_PORT:-8889}"
    - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
    - "${CMS_LOADER_PORT:-9995}:${CMS_LOADER_PORT:-9995}"
```

## Out of Scope

- Reverse proxy / nginx configuration (CMS-Loader port is exposed directly).
- TLS termination for CMS-Loader.
- CMS-Loader version pinning automation (pin manually via `CMS_LOADER_VERSION` in `.env`).
