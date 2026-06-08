# CMS-Loader Base Path Configuration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `CMS_LOADER_BASE_PATH` as a configurable `.env` variable so operators can build cms-loader's frontend to serve from a sub-path (e.g. `/cms/`) when behind a reverse proxy.

**Architecture:** `VITE_BASE_PATH` is a Vite build-time variable baked into the frontend bundle — the server needs no runtime changes. The pattern mirrors the existing `CMS_LOADER_VERSION` ARG: the user sets `CMS_LOADER_BASE_PATH` in `.env`, Docker Compose passes it as a build arg, and the Dockerfile forwards it to `pnpm run build` as `VITE_BASE_PATH`.

**Tech Stack:** Docker multi-stage build, Docker Compose v2, Vite (CMS-Loader upstream).

---

## Files

| Action | Path |
|--------|------|
| Modify | `Dockerfile` (lines 38, 56) |
| Modify | `docker/docker-compose.prod.yml` (lines 48, 64) |
| Modify | `.env.example` (after line 224) |

---

### Task 1: Add `ARG CMS_LOADER_BASE_PATH` to the Dockerfile

**Files:**
- Modify: `Dockerfile:38-56`

- [ ] **Step 1: Add the ARG declaration after line 38**

In `Dockerfile`, the current lines 38-57 read:

```dockerfile
ARG CMS_LOADER_VERSION=main

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
...
EOF

RUN git clone --branch "${CMS_LOADER_VERSION}" --depth 1 \
        https://github.com/AresLOLXD/CMS-Loader.git /build && \
    cd /build && \
    pnpm install && \
    pnpm run build && \
    pnpm prune --prod --ignore-scripts
```

Change to:

```dockerfile
ARG CMS_LOADER_VERSION=main
ARG CMS_LOADER_BASE_PATH=/

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
...
EOF

RUN git clone --branch "${CMS_LOADER_VERSION}" --depth 1 \
        https://github.com/AresLOLXD/CMS-Loader.git /build && \
    cd /build && \
    pnpm install && \
    VITE_BASE_PATH="${CMS_LOADER_BASE_PATH}" pnpm run build && \
    pnpm prune --prod --ignore-scripts
```

Two edits:
1. Add `ARG CMS_LOADER_BASE_PATH=/` on line 39 (right after `ARG CMS_LOADER_VERSION=main`).
2. Prefix `pnpm run build` with `VITE_BASE_PATH="${CMS_LOADER_BASE_PATH}" `.

- [ ] **Step 2: Verify the diff looks right**

```bash
git diff Dockerfile
```

Expected: two hunks — one adding the `ARG` line, one adding the env prefix to `pnpm run build`.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat(docker): pass CMS_LOADER_BASE_PATH to cms-loader frontend build"
```

---

### Task 2: Add `CMS_LOADER_BASE_PATH` build arg to docker-compose.prod.yml

**Files:**
- Modify: `docker/docker-compose.prod.yml:48,64`

The file has two `build.args` blocks — one for `db-init` (line 48) and one for `cms` (line 64). Both currently look like:

```yaml
        CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
```

- [ ] **Step 1: Add the new arg to both blocks**

After each `CMS_LOADER_VERSION` line, add:

```yaml
        CMS_LOADER_BASE_PATH: ${CMS_LOADER_BASE_PATH:-/}
```

The `db-init` block becomes:

```yaml
      args:
        CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
        CMS_LOADER_BASE_PATH: ${CMS_LOADER_BASE_PATH:-/}
```

The `cms` block becomes identical.

- [ ] **Step 2: Verify the diff looks right**

```bash
git diff docker/docker-compose.prod.yml
```

Expected: two hunks, each adding one `CMS_LOADER_BASE_PATH` line under `CMS_LOADER_VERSION`.

- [ ] **Step 3: Commit**

```bash
git add docker/docker-compose.prod.yml
git commit -m "feat(docker): forward CMS_LOADER_BASE_PATH build arg in compose"
```

---

### Task 3: Document `CMS_LOADER_BASE_PATH` in `.env.example`

**Files:**
- Modify: `.env.example` (after line 224)

- [ ] **Step 1: Add the new variable at the end of the CMS-LOADER section**

The section currently ends at line 224:

```dotenv
# Git branch or tag to clone for CMS-Loader (default: main).
# Pin to a release tag in production, e.g. v1.0.0.
# This is a build-time argument — changing it requires rebuilding the image.
CMS_LOADER_VERSION=main
```

Append after that block:

```dotenv

# Sub-path prefix when cms-loader is not served at the domain root (build-time).
# Example: /cms/  — the reverse proxy must forward /cms/* to the loader port.
# Default: / (served at domain root). Changing this requires rebuilding the image.
CMS_LOADER_BASE_PATH=/
```

- [ ] **Step 2: Verify the diff looks right**

```bash
git diff .env.example
```

Expected: one hunk adding the blank line + comment + variable at the end of the CMS-LOADER section.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add CMS_LOADER_BASE_PATH to .env.example"
```

---

## Done

All three files are updated. To verify the full change end-to-end, rebuild the image with a non-default base path and confirm the built `index.html` references assets at that path:

```bash
docker build --build-arg CMS_LOADER_BASE_PATH=/cms/ -t cms-loader-test . \
  && docker run --rm cms-loader-test \
       grep -o 'src="/cms/assets/[^"]*"' /home/cmsuser/cms-loader/client/dist/index.html \
  | head -3
```

Expected: asset URLs starting with `/cms/assets/`.
