# CMS-Loader Docker Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate CMS-Loader (Node.js bulk-user-importer web app) into the CMS production Docker image as an opt-in supervisord-managed service.

**Architecture:** A new `loader-builder` Dockerfile stage clones, builds, and prunes CMS-Loader; only the compiled `dist/` and production `node_modules/` are copied to the runtime stage. `generate_config.py` conditionally appends a `[program:cmsloader]` block to the generated `supervisord.conf` when the three required env vars are present. Port 9995 is exposed via docker-compose.

**Tech Stack:** Python 3.11 (generate_config.py), Docker multi-stage build, supervisord, Node.js 20, pnpm, TypeScript (CMS-Loader upstream).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docker/test_generate_config.py` | Modify | Add tests for CMS-Loader supervisord block (opt-in and opt-out) |
| `docker/generate_config.py` | Modify | Add `[program:cmsloader]` generation logic to `generate_supervisord_conf()` |
| `Dockerfile` | Modify | Add `loader-builder` stage; add 3 `COPY --from=loader-builder` directives in runtime stage |
| `.env.example` | Modify | Add `CMS-LOADER` section with 4 variables |
| `docker/docker-compose.prod.yml` | Modify | Add port `${CMS_LOADER_PORT:-9995}` and build arg `CMS_LOADER_VERSION` to `cms` service |

---

## Task 1: Failing tests for CMS-Loader supervisord block

**Files:**
- Modify: `docker/test_generate_config.py`

- [ ] **Step 1: Add three failing tests to `docker/test_generate_config.py`**

Append at the end of the file:

```python
def test_supervisord_no_cmsloader_by_default(monkeypatch):
    # CMS-Loader must NOT appear when credentials are absent.
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsloader" not in conf
    assert "cms-loader" not in conf


def test_supervisord_cmsloader_all_vars_set(monkeypatch):
    # CMS-Loader appears when all three credentials are provided.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        "CMS_LOADER_ADMIN_PASSWORD": "hunter2",
    })
    conf = gc.generate_supervisord_conf()
    assert "[program:cmsloader]" in conf
    assert "node dist/index.js" in conf
    assert "SESSION_SECRET=" in conf
    assert "ADMIN_USER=" in conf
    assert "ADMIN_PASSWORD=" in conf
    assert 'NODE_ENV="production"' in conf
    assert "directory=/home/cmsuser/cms-loader" in conf


def test_supervisord_cmsloader_partial_vars_skipped(monkeypatch):
    # Missing any one credential → CMS-Loader must not appear.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        # CMS_LOADER_ADMIN_PASSWORD intentionally omitted
    })
    conf = gc.generate_supervisord_conf()
    assert "cmsloader" not in conf


def test_supervisord_cmsloader_custom_port(monkeypatch):
    # Custom port is reflected in the environment string.
    _set(monkeypatch, {
        "CMS_LOADER_SESSION_SECRET": "supersecret32charslongenoughXXXX",
        "CMS_LOADER_ADMIN_USER": "admin",
        "CMS_LOADER_ADMIN_PASSWORD": "hunter2",
        "CMS_LOADER_PORT": "9000",
    })
    conf = gc.generate_supervisord_conf()
    assert 'PORT="9000"' in conf
```

- [ ] **Step 2: Run the new tests and confirm they FAIL**

```bash
cd /var/home/areslolxd/Documentos/cms
pytest docker/test_generate_config.py::test_supervisord_no_cmsloader_by_default \
       docker/test_generate_config.py::test_supervisord_cmsloader_all_vars_set \
       docker/test_generate_config.py::test_supervisord_cmsloader_partial_vars_skipped \
       docker/test_generate_config.py::test_supervisord_cmsloader_custom_port \
       -v
```

Expected: 3–4 FAILs (the no_cmsloader test may pass already; the rest must fail).

---

## Task 2: Implement CMS-Loader block in generate_config.py

**Files:**
- Modify: `docker/generate_config.py:164-224` (`generate_supervisord_conf`)

- [ ] **Step 1: Add CMS-Loader block to `generate_supervisord_conf()`**

In `docker/generate_config.py`, find the end of `generate_supervisord_conf()` — just before `return "\n".join(blocks)`. Insert the following block:

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

The full end of the function should look like:

```python
    if telegram_configured:
        blocks.append(
            program("cmstelegrambot", f"cmsTelegramBot 0 -c {contest_id}", 65)
        )

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

    return "\n".join(blocks)
```

- [ ] **Step 2: Run ALL tests and confirm they pass**

```bash
pytest docker/test_generate_config.py -v
```

Expected: all tests PASS (including pre-existing ones — verify none regressed).

- [ ] **Step 3: Commit**

```bash
git add docker/generate_config.py docker/test_generate_config.py
git commit -m "feat: add CMS-Loader supervisord block to generate_config"
```

---

## Task 3: Dockerfile — loader-builder stage

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Insert `loader-builder` stage after `rekarel-builder`**

In `Dockerfile`, after line 33 (end of the `rekarel-builder` stage, before `# ─── Stage 2: CMS runtime`), insert:

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
    apt-get install -y build-essential curl ca-certificates git
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

Update the comment on the old Stage 2 to Stage 3:

```dockerfile
# ─── Stage 3: CMS runtime ─────────────────────────────────────────────────────
```

- [ ] **Step 2: Copy CMS-Loader artifacts to runtime stage**

In the runtime stage, after the three `COPY --from=rekarel-builder` lines (around line 103), add:

```dockerfile
# Copy CMS-Loader build artifacts from the loader-builder stage.
# Only dist/ and production node_modules/ are copied — TypeScript compiler
# and pnpm are discarded.
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/dist         /home/cmsuser/cms-loader/dist
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/node_modules /home/cmsuser/cms-loader/node_modules
COPY --from=loader-builder --chown=cmsuser:cmsuser \
    /build/package.json /home/cmsuser/cms-loader/package.json
```

- [ ] **Step 3: Verify Dockerfile syntax**

```bash
docker build --no-cache --target loader-builder \
    -f /var/home/areslolxd/Documentos/cms/Dockerfile \
    /var/home/areslolxd/Documentos/cms 2>&1 | tail -5
```

Expected: `Successfully built <id>` (or equivalent BuildKit output with no errors).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: add loader-builder stage for CMS-Loader"
```

---

## Task 4: .env.example — CMS-LOADER section

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append CMS-LOADER section to `.env.example`**

At the end of `.env.example`, after the `LOGGING` section, add:

```bash
# -----------------------------------------------------------
# CMS-LOADER (optional — bulk user/participation importer)
# -----------------------------------------------------------

# Random 32+ character string to sign session cookies.
# Generate one with: openssl rand -base64 32
# If unset, CMS-Loader will not start.
CMS_LOADER_SESSION_SECRET=

# Administrator credentials for the CMS-Loader web UI.
CMS_LOADER_ADMIN_USER=
CMS_LOADER_ADMIN_PASSWORD=

# Port where CMS-Loader listens (default: 9995).
CMS_LOADER_PORT=9995

# Git branch or tag to clone for CMS-Loader (default: main).
# Pin to a release tag in production, e.g. v1.0.0.
CMS_LOADER_VERSION=main
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "feat: add CMS-LOADER env vars to .env.example"
```

---

## Task 5: docker-compose.prod.yml — port and build arg

**Files:**
- Modify: `docker/docker-compose.prod.yml`

- [ ] **Step 1: Add build arg `CMS_LOADER_VERSION` to the `cms` service**

In `docker/docker-compose.prod.yml`, find the `cms` service's `build:` block:

```yaml
  cms:
    build:
      context: ..
```

Replace with:

```yaml
  cms:
    build:
      context: ..
      args:
        CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
```

- [ ] **Step 2: Add CMS-Loader port to the `cms` service**

In the same file, find the `ports:` block of the `cms` service and append the CMS-Loader port:

```yaml
    ports:
      - "${CMS_CWS_HTTP_PORT:-8888}:${CMS_CWS_HTTP_PORT:-8888}"
      - "${CMS_AWS_HTTP_PORT:-8889}:${CMS_AWS_HTTP_PORT:-8889}"
      - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
      - "${CMS_LOADER_PORT:-9995}:${CMS_LOADER_PORT:-9995}"
```

- [ ] **Step 3: Commit**

```bash
git add docker/docker-compose.prod.yml
git commit -m "feat: expose CMS-Loader port and version arg in docker-compose.prod.yml"
```

---

## Task 6: Full build smoke test

- [ ] **Step 1: Build the full production image**

```bash
cd /var/home/areslolxd/Documentos/cms
docker build -t cms-prod-test .
```

Expected: build succeeds with no errors. Confirm the `loader-builder` stage appears in output.

- [ ] **Step 2: Verify CMS-Loader files are present in the runtime image**

```bash
docker run --rm cms-prod-test ls /home/cmsuser/cms-loader/
```

Expected output includes: `dist/  node_modules/  package.json`

- [ ] **Step 3: Verify opt-out — no CMS_LOADER_* vars → cmsloader absent from supervisord.conf**

```bash
docker run --rm \
  -e CMS_DB_URL=postgresql+psycopg2://cms:secret@db:5432/cmsdb \
  -e CMS_SECRET_KEY=abcdef0123456789abcdef0123456789 \
  -e CMS_CONTEST_ID=1 \
  cms-prod-test bash -c "
    python3 /home/cmsuser/generate_config.py 2>/dev/null
    grep 'cmsloader' /home/cmsuser/cms/etc/supervisord.conf \
      && echo 'FAIL: cmsloader found unexpectedly' \
      || echo 'PASS: cmsloader not present (correct)'
  "
```

Expected: `PASS: cmsloader not present (correct)`

- [ ] **Step 4: Verify opt-in — with CMS_LOADER_* vars → cmsloader present in supervisord.conf**

```bash
docker run --rm \
  -e CMS_DB_URL=postgresql+psycopg2://cms:secret@db:5432/cmsdb \
  -e CMS_SECRET_KEY=abcdef0123456789abcdef0123456789 \
  -e CMS_CONTEST_ID=1 \
  -e CMS_LOADER_SESSION_SECRET=supersecretvaluethatismorethan32chars \
  -e CMS_LOADER_ADMIN_USER=admin \
  -e CMS_LOADER_ADMIN_PASSWORD=hunter2 \
  cms-prod-test bash -c "python3 /home/cmsuser/generate_config.py 2>/dev/null; grep 'program:cmsloader' /home/cmsuser/cms/etc/supervisord.conf"
```

Expected: `[program:cmsloader]` is printed.

- [ ] **Step 5: Clean up test image**

```bash
docker rmi cms-prod-test
```
