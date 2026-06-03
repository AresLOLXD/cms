# Ranking Split Service & Selective Rebuild Design

**Date:** 2026-06-02  
**Status:** Approved

## Problem

1. Every code change triggers a full Docker image rebuild, including re-fetching `cms_rekarel` from GitHub and re-running `./install.py cms`, even when only `cmsranking/` changed.
2. `_do_up()` always prompts "Use local PostgreSQL container?" with a hardcoded `n` default, ignoring the `CMS_USE_LOCALDB` value already persisted in `.env`.

## Solution Overview

Split `cmsRankingWebServer` into its own Docker service (`ranking`) with a lightweight Dockerfile. Update `_do_up()` to offer a 4-option rebuild menu and fix the localdb default.

---

## Architecture

### Services (docker-compose.prod.yml)

| Service | Image | What it runs |
|---|---|---|
| `cms` | Full `Dockerfile` (unchanged) | All CMS services via supervisord, **excluding** `cmsRankingWebServer` |
| `ranking` | New `Dockerfile.ranking` | Only `cmsRankingWebServer` |
| `db` (profile) | `postgres:17-alpine` | Unchanged |
| `db-init` | Full `Dockerfile` | Unchanged (`cmsSetupDB`) |

Both `cms` and `ranking` are on the same Compose network, so `cms` reaches `ranking` at hostname `ranking`.

### Dockerfile.ranking

Lightweight image — no compilers (no Java, GHC, Mono, PHP, Rust, PyPy), no isolate, no rekarel, no CMS-Loader. Steps:

1. `ubuntu:noble` base
2. `apt-get install python3 python3-pip python3-venv` (+ minimal deps)
3. Copy `install.py`, `constraints.txt` → `./install.py venv`
4. Copy source → `pip install .` (installs `cmsranking` + `cmscommon`)
5. Copy `docker/generate_config.py`, `docker/entrypoint.sh`
6. `CMD ["cmsRankingWebServer"]` — runs directly, no supervisord

`entrypoint.sh` runs `generate_config.py` before exec. In the ranking container, `generate_config.py` only writes `cms_ranking.toml` (the supervisord block is skipped because `CMS_CONTEST_ID` is not set in this container).

Build time for a ranking-only change: seconds (no compiler toolchains, no rekarel fetch).

### Ranking config connectivity

`generate_config.py` currently hardcodes `localhost` as the ranking hostname in `cms.toml`:
```
rankings = ["http://user:pass@localhost:8890/"]
```

Change: read `CMS_RWS_HOST` env var (default `localhost`). In `docker-compose.prod.yml`, set `CMS_RWS_HOST=ranking` for the `cms` service so it connects to the `ranking` container over the Docker network.

Backward compat: `CMS_RWS_HOST` defaults to `localhost`, so non-Docker installs are unaffected.

### Port exposure

`CMS_RWS_HTTP_PORT` (default 8890) moves from the `cms` service to the `ranking` service in `docker-compose.prod.yml`. No change visible to the outside world.

### supervisord.conf

Remove `cmsrankingwebserver` from `generate_supervisord_conf()` in `generate_config.py`. The ranking container runs `cmsRankingWebServer` directly as the container CMD (not via supervisord).

---

## _do_up() Changes (docker/_lib.sh)

### Fix localdb default

Read current `CMS_USE_LOCALDB` from `.env` before prompting:

```bash
local current_localdb
current_localdb="$(_env_var CMS_USE_LOCALDB false)"
local localdb_default
[[ "$current_localdb" == "true" ]] && localdb_default="y" || localdb_default="n"
if ask_yes_no "Use local PostgreSQL container?" "$localdb_default"; then
```

### Rebuild menu

Replace the single "Rebuild image?" yes/no with a 4-option menu:

```
Rebuild?
  1) No  (default)
  2) All services
  3) Ranking only
  4) CMS only
```

`--build ranking` or `--build cms` are passed as service-specific build targets to `docker compose up`. Option 2 passes `--build` without a target (rebuilds all).

No changes to `up.sh`, `down.sh`, or `restart.sh` — they all call `_do_up()` unchanged.

---

## File Map

| File | Action |
|---|---|
| `Dockerfile.ranking` | Create — lightweight ranking image |
| `docker-compose.prod.yml` | Add `ranking` service; move RWS port; add `CMS_RWS_HOST=ranking` to `cms` env |
| `docker/generate_config.py` | Read `CMS_RWS_HOST`; remove `cmsrankingwebserver` from supervisord |
| `docker/_lib.sh` → `_do_up()` | Fix localdb default; replace rebuild yes/no with 4-option menu |
| `.env.example` | Document `CMS_RWS_HOST` (optional, blank = localhost) |

---

## Out of Scope

- `docker-compose.dev.yml` and `docker-compose.test.yml` — keep ranking inside the single container for simplicity in dev/test.
- `cms-dev.sh` — unchanged (always rebuilds the single dev container).
