# README Fork Navigation — Design Spec

**Date:** 2026-05-19
**Status:** Approved

## Overview

Rewrite the README as a coherent, delegate-friendly entry point to the OMI fork of CMS. The goal is that a non-technical delegate can go from zero to a running contest by reading the README alone, while technical users can navigate to the relevant doc for each fork-specific feature without digging through the repo.

## Audience

- **Primary:** OMI contest organizers and delegates with limited Docker/Linux experience.
- **Secondary:** Technical contributors who want to understand what this fork adds over upstream.

## Structure

```
# Contest Management System — OMI Fork

[badges]

## Quick Start              ← 4 steps, delegate-friendly
## About this fork          ← context, license, open to all
## Features                 ← navigation table with links
## Deploy with Docker        ← full guide (stays in README)
## Helper scripts            ← existing table, unchanged
## Testimonials / Support    ← upstream content, unchanged
```

## Section: Quick Start

Four numbered steps that take a delegate from zero to a running system:

1. Clone the repo.
2. `cp .env.example .env` — fill in the values marked `CHANGE_ME`.
3. `./up.sh`
4. Open `http://your-server:8889` (Admin) and `http://your-server:8888` (Contest).

Followed by a visible callout: *"Need more detail? See [Deploy with Docker](#deploy-with-docker)."*

## Section: About this fork

> This is a fork of [cms-dev/cms](https://github.com/cms-dev/cms), maintained for the
> [Olimpiada Mexicana de Informática (OMI)](https://www.olimpiadadeinformatica.org.mx/).
> It is distributed under the same license as the original project (AGPL-3.0).
>
> While it was built with the OMI's needs in mind, **anyone can use it**. The main additions
> over upstream are a Docker-based deployment workflow, a bundled Rekarel compiler, a
> CMS-Loader integration for bulk user/participation import, and a pre-configured ranking
> server with Mexican state flags and auto-team registration.

## Section: Features table

| Feature | Description | Doc |
|---|---|---|
| Docker deployment | Stand up the full system with `./up.sh` | [Deploy with Docker](#deploy-with-docker) |
| Helper scripts | `up`, `down`, `logs`, `restart`, `contest`, `sync-upstream` | [docs/docker-scripts.md](docs/docker-scripts.md) |
| CMS-Loader | Bulk-import users and participations via CSV from the browser | [docs/cms-loader.md](docs/cms-loader.md) |
| Rekarel | Karel compiler and interpreter bundled in the Docker image | [docs/rekarel.md](docs/rekarel.md) |
| Ranking: custom logo | Replace the ranking server logo without touching source code | [docs/RankingWebServer.rst](docs/RankingWebServer.rst) |
| Ranking: flags and teams | Real Mexican state flags + automatic team registration on startup | [docs/ranking-mexico.md](docs/ranking-mexico.md) |

## New docs to create

### `docs/cms-loader.md`

- What CMS-Loader is, with link to the original project: https://github.com/AresLOLXD/CMS-Loader
- How to enable it: set `CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER`, `CMS_LOADER_ADMIN_PASSWORD` in `.env`
- Access URL: `http://your-server:9995` (port configurable via `CMS_LOADER_PORT`)
- Expected CSV format for users and participations
- Note: service is opt-in — silently skipped if credentials are not set

### `docs/rekarel.md`

- What Rekarel is, with links to the original projects:
  - Rekarel CLI: https://github.com/kishtarn555/rekarel-js
  - Karel C++ interpreter: https://github.com/kishtarn555/rekarel-cpp-interpreter
- Already bundled in the Docker image — no installation required
- How to verify it is available: `rekarel --version`, `karel`
- Versions bundled (to be filled at implementation time from Dockerfile ARGs)

### `docs/ranking-mexico.md`

- How auto-team registration works on ranking server startup (reads `cmsranking/flags/` on boot)
- How to add or replace state flag images in `cmsranking/flags/`
- How to regenerate all 32 Mexican state flags using `cmscontrib/DownloadMexicanStateFlags.py`
- Link to `docs/RankingWebServer.rst` for custom logo instructions

### `docs/RankingWebServer.rst` (addition only)

Add a "Custom logo" section explaining:
- Place `logo.png` (or `.jpg`, `.gif`, `.bmp`) in `lib_dir` (default: `$VIRTUAL_ENV/lib/ranking/`)
- The server serves it automatically at `/logo`
- Remove the file to revert to the bundled default

## Files changed

| File | Change |
|---|---|
| `README.md` | Full rewrite from fork perspective; structure above |
| `docs/cms-loader.md` | New user-facing doc |
| `docs/rekarel.md` | New user-facing doc |
| `docs/ranking-mexico.md` | New user-facing doc |
| `docs/RankingWebServer.rst` | Add custom logo section |

## Out of scope

- Changes to any Python, Docker, or shell code
- Modifying upstream sections (Testimonials, Support, original Download)
- Translating docs to Spanish (English-only per project convention)
