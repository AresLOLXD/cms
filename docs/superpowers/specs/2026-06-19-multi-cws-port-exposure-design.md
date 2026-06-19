# Multi-CWS Port Exposure — Design Spec

**Date:** 2026-06-19
**Status:** Approved

## Problem

When `CMS_CWS_COUNT > 1`, `generate_config.py` correctly assigns each CWS shard an HTTP
port of `CMS_CWS_HTTP_PORT + i` (i = 0 … COUNT-1) inside `cms.toml`. However,
`docker-compose.prod.yml` only exposes the base port `CMS_CWS_HTTP_PORT`. Shards 1, 2, …
are unreachable from outside the container — including from the host-side reverse proxy
that needs to route traffic to them.

## Chosen Approach

Compute `CMS_CWS_HTTP_PORT_END = CMS_CWS_HTTP_PORT + CMS_CWS_COUNT - 1` in `_lib.sh`
and export it to the environment before any `docker compose` call. Update the compose file
to use Docker's native port-range syntax so the full shard range is mapped automatically.

When `COUNT=1`, `PORT_END == PORT` and Docker treats a single-element range as a single
port — identical behaviour to today.

## Files Changed

### `docker/_lib.sh`

After reading `PROJECT_NAME` (line ~17), export the derived variable:

```bash
_CWS_BASE_PORT="$(_env_var CMS_CWS_HTTP_PORT 8888)"
_CWS_COUNT="$(_env_var CMS_CWS_COUNT 1)"
export CMS_CWS_HTTP_PORT_END=$(( _CWS_BASE_PORT + _CWS_COUNT - 1 ))
```

This runs at source-time, so the value is available for every script that sources `_lib.sh`
(`up.sh`, `down.sh`, `restart.sh`, `status.sh`, `logs.sh`, etc.).

### `docker/docker-compose.prod.yml`

Replace the single CWS port mapping with a range:

```yaml
# before
- "${CMS_CWS_HTTP_PORT:-8888}:${CMS_CWS_HTTP_PORT:-8888}"

# after
- "${CMS_CWS_HTTP_PORT:-8888}-${CMS_CWS_HTTP_PORT_END:-8888}:${CMS_CWS_HTTP_PORT:-8888}-${CMS_CWS_HTTP_PORT_END:-8888}"
```

`CMS_AWS_HTTP_PORT` and `CMS_LOADER_PORT` lines are unchanged.

**Limitation:** If `docker compose` is invoked directly (bypassing `up.sh`/`_lib.sh`),
`CMS_CWS_HTTP_PORT_END` will be unset and fall back to the `:-8888` default, which only
exposes shard 0. This is the same behaviour as before, not a regression. Operators should
always use the provided scripts.

### `.env.example`

Expand the `CMS_CWS_COUNT` comment to include:

- What ports are exposed on the host (`CMS_CWS_HTTP_PORT` … `CMS_CWS_HTTP_PORT + COUNT - 1`)
- Port-conflict warning: with the defaults (`CMS_CWS_HTTP_PORT=8888`,
  `CMS_AWS_HTTP_PORT=8889`), setting `CMS_CWS_COUNT=2` causes a conflict because shard 1
  lands on 8889. When `COUNT > 1`, set `CMS_AWS_HTTP_PORT` to
  `CMS_CWS_HTTP_PORT + CMS_CWS_COUNT` or higher.

### `docs/docker-scripts.md`

Add a new section **"Scaling Contest Web Server"** between the project-name configuration
section and the troubleshooting section. Content:

1. What `CMS_CWS_COUNT` does and when to use it (load balancer / reverse proxy in front).
2. Which host ports are opened (`CMS_CWS_HTTP_PORT` … `+COUNT-1`).
3. Port-conflict warning (same as `.env.example`).
4. Minimal nginx `upstream` + `proxy_pass` example balancing across all shards.
5. Note to set `CMS_NUM_PROXIES_USED=1` so CMS logs real contestant IPs.

## Out of Scope

- Adding nginx as a Docker Compose service (user provides their own reverse proxy).
- Changing the default port values or the AWS/RWS port scheme.
- Any changes to `generate_config.py` (already correct).
