# Docker Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the CMS Docker setup so that a production server can be configured entirely from outside the container using environment variables or a mounted config file, with the cms_rekarel Karel language plugin pre-installed.

**Architecture:** A two-stage Dockerfile (rekarel-builder → runtime) bakes the rekarel compiler and C++ interpreter into the image. A Python config-generation script (`generate_config.py`) runs at container start via `entrypoint.sh`, producing `cms.toml`, `cms_ranking.toml`, and `supervisord.conf` from environment variables. All eight CMS services are managed by supervisord as a single container. Existing dev/test docker-compose files remain fully functional.

**Tech Stack:** Docker BuildKit multi-stage, supervisord (system package), Python 3.11, Node.js 18 (ubuntu:noble apt), @rekarel/cli (npm), rekarel-cpp-interpreter v2.3.1 (compiled from source with g++ -static).

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `docker/generate_config.py` | Generate cms.toml, cms_ranking.toml, supervisord.conf from env vars |
| Create | `docker/test_generate_config.py` | Unit tests for generate_config.py |
| Create | `docker/entrypoint.sh` | Container startup: run generate_config.py, validate config, exec CMD |
| Create | `docker/docker-compose.prod.yml` | Production compose (Postgres profile + CMS service) |
| Modify | `Dockerfile` | Multi-stage: rekarel-builder + runtime; add nodejs, supervisord, entrypoint |
| Done ✅ | `README.md` | Deploy with Docker section |
| Done ✅ | `.env.example` | Documented environment variables |
| Done ✅ | `.gitignore` | Added `.env` |

---

## Task 1: docker/generate_config.py

**Files:**
- Create: `docker/generate_config.py`
- Create: `docker/test_generate_config.py`

- [ ] **Step 1.1: Write the tests**

Create `docker/test_generate_config.py`:

```python
"""Unit tests for docker/generate_config.py."""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import generate_config as gc

VALID_ENV = {
    "CMS_DB_URL": "postgresql+psycopg2://cms:secret@db:5432/cmsdb",
    "CMS_SECRET_KEY": "abcdef0123456789abcdef0123456789",
    "CMS_CONTEST_ID": "1",
}


def _set(monkeypatch, extra=None):
    for k, v in VALID_ENV.items():
        monkeypatch.setenv(k, v)
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)


def test_validate_missing_db_url(monkeypatch):
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.setenv("CMS_SECRET_KEY", "abcdef0123456789abcdef0123456789")
    with pytest.raises(SystemExit):
        gc.validate_required()


def test_validate_insecure_secret_key(monkeypatch):
    monkeypatch.setenv("CMS_DB_URL", "postgresql+psycopg2://x:y@z/db")
    monkeypatch.setenv("CMS_SECRET_KEY", gc.INSECURE_SECRET_KEY)
    with pytest.raises(SystemExit):
        gc.validate_required()


def test_validate_passes_with_valid_env(monkeypatch):
    _set(monkeypatch)
    gc.validate_required()  # must not raise


def test_cms_toml_defaults(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_toml()
    assert 'url = "postgresql+psycopg2://cms:secret@db:5432/cmsdb"' in toml
    assert "listen_port = [8888]" in toml
    assert "listen_port = 8889" in toml
    assert 'Worker = [["localhost", 26000]]' in toml
    assert 'ContestWebServer = [["localhost", 21000]]' in toml
    assert 'AdminWebServer = [["localhost", 21100]]' in toml


def test_cms_toml_multiple_cws(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COUNT": "3", "CMS_CWS_HTTP_PORT": "8888"})
    toml = gc.generate_cms_toml()
    assert "listen_port = [8888, 8889, 8890]" in toml
    assert '["localhost", 21000], ["localhost", 21001], ["localhost", 21002]' in toml


def test_cms_toml_multiple_workers(monkeypatch):
    _set(monkeypatch, {"CMS_WORKER_COUNT": "3"})
    toml = gc.generate_cms_toml()
    assert '["localhost", 26000], ["localhost", 26001], ["localhost", 26002]' in toml


def test_cms_toml_custom_aws_port(monkeypatch):
    _set(monkeypatch, {"CMS_AWS_HTTP_PORT": "9000"})
    toml = gc.generate_cms_toml()
    assert "listen_port = 9000" in toml


def test_cms_toml_proxy_url_contains_rws_creds(monkeypatch):
    _set(monkeypatch, {"CMS_RWS_USERNAME": "myuser", "CMS_RWS_PASSWORD": "mypass"})
    toml = gc.generate_cms_toml()
    assert "myuser:mypass@localhost" in toml


def test_ranking_toml_defaults(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_ranking_toml()
    assert 'bind_address = "0.0.0.0"' in toml
    assert "http_port = 8890" in toml


def test_ranking_toml_custom(monkeypatch):
    _set(monkeypatch, {
        "CMS_RWS_USERNAME": "myuser",
        "CMS_RWS_PASSWORD": "mypassword",
        "CMS_RWS_HTTP_PORT": "9090",
    })
    toml = gc.generate_cms_ranking_toml()
    assert 'username = "myuser"' in toml
    assert 'password = "mypassword"' in toml
    assert "http_port = 9090" in toml


def test_supervisord_single_cws_worker(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsLogService 0" in conf
    assert "cmsWorker 0" in conf
    assert "cmsContestWebServer 0 1" in conf
    assert "cmsAdminWebServer 0" in conf
    # LogService must have the lowest priority number (starts first)
    log_priority = int(conf.split("cmsLogService")[0].rsplit("priority=", 1)[-1].split("\n")[0])
    assert log_priority <= 20


def test_supervisord_multiple_cws(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_COUNT": "2", "CMS_CONTEST_ID": "5"})
    conf = gc.generate_supervisord_conf()
    assert "cmsContestWebServer 0 5" in conf
    assert "cmsContestWebServer 1 5" in conf


def test_supervisord_multiple_workers(monkeypatch):
    _set(monkeypatch, {"CMS_WORKER_COUNT": "2"})
    conf = gc.generate_supervisord_conf()
    assert "cmsWorker 0" in conf
    assert "cmsWorker 1" in conf
```

- [ ] **Step 1.2: Run tests to confirm they fail (module not found)**

```bash
cd /var/home/areslolxd/Documentos/cms
python -m pytest docker/test_generate_config.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'generate_config'`

- [ ] **Step 1.3: Write the implementation**

Create `docker/generate_config.py`:

```python
#!/usr/bin/env python3
"""Generate cms.toml, cms_ranking.toml, and supervisord.conf from environment variables.

Called by entrypoint.sh at container startup. If CMS_CONFIG already points to
an existing file, cms.toml generation is skipped (preserves dev/test compat).
supervisord.conf is only generated when CMS_CONTEST_ID is set.
"""

import os
import sys

INSECURE_SECRET_KEY = "8e045a51e4b102ea803c06f92841a1fb"


def _require(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        print(f"ERROR: required environment variable {var!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _get(var: str, default: str) -> str:
    return os.environ.get(var) or default


def _get_int(var: str, default: int) -> int:
    return int(os.environ.get(var) or default)


def validate_required() -> None:
    _require("CMS_DB_URL")
    secret_key = _require("CMS_SECRET_KEY")
    if secret_key == INSECURE_SECRET_KEY:
        print(
            "ERROR: CMS_SECRET_KEY is set to the public example value.\n"
            "Generate a real key with:\n"
            "  python3 -c 'from cmscommon import crypto; print(crypto.get_hex_random_key())'",
            file=sys.stderr,
        )
        sys.exit(1)


def generate_cms_toml() -> str:
    db_url = _require("CMS_DB_URL")
    secret_key = _require("CMS_SECRET_KEY")
    listen_addr = _get("CMS_LISTEN_ADDRESS", "0.0.0.0")
    num_proxies = _get_int("CMS_NUM_PROXIES_USED", 0)
    log_debug = _get("CMS_LOG_DEBUG", "false").lower()

    cws_count = _get_int("CMS_CWS_COUNT", 1)
    worker_count = _get_int("CMS_WORKER_COUNT", 1)
    cws_http_port = _get_int("CMS_CWS_HTTP_PORT", 8888)
    cws_rpc_port = _get_int("CMS_CWS_RPC_PORT", 21000)
    aws_http_port = _get_int("CMS_AWS_HTTP_PORT", 8889)
    aws_rpc_port = _get_int("CMS_AWS_RPC_PORT", 21100)
    rws_http_port = _get_int("CMS_RWS_HTTP_PORT", 8890)
    rws_username = _get("CMS_RWS_USERNAME", "rws")
    rws_password = _get("CMS_RWS_PASSWORD", "")

    worker_entries = ", ".join(f'["localhost", {26000 + i}]' for i in range(worker_count))
    cws_entries = ", ".join(f'["localhost", {cws_rpc_port + i}]' for i in range(cws_count))
    cws_ports = "[" + ", ".join(str(cws_http_port + i) for i in range(cws_count)) + "]"
    cws_addrs = "[" + ", ".join(f'"{listen_addr}"' for _ in range(cws_count)) + "]"

    return f"""\
[global]
file_log_debug = {log_debug}
stream_log_detailed = false

[services]
LogService = [["localhost", 29000]]
ResourceService = [["localhost", 28000]]
ScoringService = [["localhost", 28500]]
Checker = [["localhost", 22000]]
EvaluationService = [["localhost", 25000]]
Worker = [{worker_entries}]
ContestWebServer = [{cws_entries}]
AdminWebServer = [["localhost", {aws_rpc_port}]]
ProxyService = [["localhost", 28600]]

[database]
url = "{db_url}"
debug = false

[worker]
keep_sandbox = false

[web_server]
secret_key = "{secret_key}"
tornado_debug = false

[contest_web_server]
listen_address = {cws_addrs}
listen_port = {cws_ports}
num_proxies_used = {num_proxies}

[admin_web_server]
listen_address = "{listen_addr}"
listen_port = {aws_http_port}
num_proxies_used = {num_proxies}

[proxy_service]
rankings = ["http://{rws_username}:{rws_password}@localhost:{rws_http_port}/"]
"""


def generate_cms_ranking_toml() -> str:
    listen_addr = _get("CMS_LISTEN_ADDRESS", "0.0.0.0")
    rws_http_port = _get_int("CMS_RWS_HTTP_PORT", 8890)
    rws_username = _get("CMS_RWS_USERNAME", "rws")
    rws_password = _get("CMS_RWS_PASSWORD", "")

    return f"""\
bind_address = "{listen_addr}"
http_port = {rws_http_port}
username = "{rws_username}"
password = "{rws_password}"
realm_name = "Scoreboard"
buffer_size = 100

[public]
show_id_column = false
"""


def generate_supervisord_conf() -> str:
    cws_count = _get_int("CMS_CWS_COUNT", 1)
    worker_count = _get_int("CMS_WORKER_COUNT", 1)
    contest_id = _require("CMS_CONTEST_ID")

    def program(name: str, command: str, priority: int) -> str:
        return (
            f"[program:{name}]\n"
            f"command={command}\n"
            f"priority={priority}\n"
            "autostart=true\n"
            "autorestart=true\n"
            "stdout_logfile=/dev/stdout\n"
            "stdout_logfile_maxbytes=0\n"
            "stderr_logfile=/dev/stderr\n"
            "stderr_logfile_maxbytes=0\n"
        )

    blocks = [
        "[supervisord]\n"
        "nodaemon=true\n"
        "logfile=/dev/null\n"
        "logfile_maxbytes=0\n"
        "\n"
        "[unix_http_server]\n"
        "file=/tmp/supervisor.sock\n"
        "\n"
        "[supervisorctl]\n"
        "serverurl=unix:///tmp/supervisor.sock\n"
        "\n"
        "[rpcinterface:supervisor]\n"
        "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n",
        program("cmslogservice", "cmsLogService 0", 10),
        program("cmsresourceservice", "cmsResourceService 0", 20),
        program("cmsscoringservice", "cmsScoringService 0", 30),
        program("cmsevaluationservice", "cmsEvaluationService 0", 30),
    ]

    for i in range(worker_count):
        blocks.append(program(f"cmsworker{i}", f"cmsWorker {i}", 40))

    blocks.append(program("cmsproxyservice", "cmsProxyService 0", 50))
    blocks.append(program("cmsrankingwebserver", "cmsRankingWebServer 0", 55))

    for i in range(cws_count):
        blocks.append(
            program(f"cmscontestwebserver{i}", f"cmsContestWebServer {i} {contest_id}", 60)
        )

    blocks.append(program("cmsadminwebserver", "cmsAdminWebServer 0", 60))

    return "\n".join(blocks)


def main() -> None:
    config_path = _get("CMS_CONFIG", "/home/cmsuser/cms/etc/cms.toml")
    ranking_config_path = _get("CMS_RANKING_CONFIG", "/home/cmsuser/cms/etc/cms_ranking.toml")
    supervisord_path = "/home/cmsuser/cms/etc/supervisord.conf"

    if os.path.isfile(config_path):
        print(f"Using existing config: {config_path}", file=sys.stderr)
    else:
        validate_required()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(generate_cms_toml())
        print(f"Generated {config_path}", file=sys.stderr)

    if os.path.isfile(ranking_config_path):
        print(f"Using existing ranking config: {ranking_config_path}", file=sys.stderr)
    else:
        os.makedirs(os.path.dirname(ranking_config_path), exist_ok=True)
        with open(ranking_config_path, "w") as f:
            f.write(generate_cms_ranking_toml())
        print(f"Generated {ranking_config_path}", file=sys.stderr)

    if os.environ.get("CMS_CONTEST_ID"):
        os.makedirs(os.path.dirname(supervisord_path), exist_ok=True)
        with open(supervisord_path, "w") as f:
            f.write(generate_supervisord_conf())
        print(f"Generated {supervisord_path}", file=sys.stderr)
    else:
        print("CMS_CONTEST_ID not set — skipping supervisord.conf generation.", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.4: Run tests and confirm they pass**

```bash
cd /var/home/areslolxd/Documentos/cms
python -m pytest docker/test_generate_config.py -v
```

Expected: all tests pass.

- [ ] **Step 1.5: Commit**

```bash
git add docker/generate_config.py docker/test_generate_config.py
git commit -m "feat(docker): add generate_config.py to build cms.toml and supervisord.conf from env vars"
```

---

## Task 2: docker/entrypoint.sh

**Files:**
- Create: `docker/entrypoint.sh`

- [ ] **Step 2.1: Create the entrypoint script**

Create `docker/entrypoint.sh`:

```bash
#!/bin/bash
set -euo pipefail

# Generate cms.toml (skipped if CMS_CONFIG already points to an existing file),
# cms_ranking.toml, and supervisord.conf (only when CMS_CONTEST_ID is set).
python3 /home/cmsuser/generate_config.py

# Validate that the config file parses without errors before starting any service.
python3 -c "import cms.conf" 2>&1 || {
    echo "ERROR: CMS config failed to parse. Check your environment variables." >&2
    exit 1
}

# Remove stale socket files left by previous container runs.
rm -f /home/cmsuser/cms/run/*.sock 2>/dev/null || true

exec "$@"
```

- [ ] **Step 2.2: Verify the script is syntactically valid**

```bash
bash -n docker/entrypoint.sh && echo "OK"
```

Expected: `OK`

- [ ] **Step 2.3: Commit**

```bash
git add docker/entrypoint.sh
git commit -m "feat(docker): add entrypoint.sh to initialize config and exec CMS"
```

---

## Task 3: Rewrite Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 3.1: Replace the Dockerfile content**

Replace the entire `Dockerfile` with:

```dockerfile
# syntax=docker/dockerfile:1
# Supported base images: ubuntu:noble, debian:bookworm.
ARG BASE_IMAGE=ubuntu:noble

# ─── Stage 1: Build rekarel compiler (Node.js) and C++ interpreter ────────────
FROM ${BASE_IMAGE} AS rekarel-builder

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y build-essential libexpat-dev curl ca-certificates
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
EOF

# Install rekarel compiler CLI.
RUN npm install -g @rekarel/cli

# Build the C++ Karel interpreter from source (no pre-built binaries for v2.3.1).
# The binary is statically linked (-static flag in the Makefile), so it has
# no runtime library dependencies and copies cleanly to the runtime stage.
RUN mkdir -p /build && \
    curl -fsSL "https://api.github.com/repos/kishtarn555/rekarel-cpp-interpreter/tarball/v2.3.1" \
        | tar xz --strip-components=1 -C /build && \
    cd /build && mkdir bin && make karel

# ─── Stage 2: CMS runtime ─────────────────────────────────────────────────────
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    PACKAGES=(
        build-essential
        cppreference-doc-en-html
        curl
        default-jdk-headless
        fp-compiler
        ghc
        git
        libcap-dev
        libffi-dev
        libpq-dev
        libyaml-dev
        mono-mcs
        nodejs
        php-cli
        postgresql-client
        pypy3
        python3
        python3-dev
        python3-pip
        python3-venv
        rustc
        shared-mime-info
        sudo
        supervisor
        wait-for-it
        zip
    )
    apt-get install -y "${PACKAGES[@]}"
EOF

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    CODENAME=$(source /etc/os-release; echo $VERSION_CODENAME)
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/isolate.asc]" \
        "http://www.ucw.cz/isolate/debian/ ${CODENAME}-isolate main" \
        >/etc/apt/sources.list.d/isolate.list
    curl https://www.ucw.cz/isolate/debian/signing-key.asc \
        >/etc/apt/keyrings/isolate.asc
    apt-get update
    apt-get install -y isolate
    sed -i 's@^cg_root .*@cg_root = /sys/fs/cgroup@' /etc/isolate
EOF

# Create cmsuser (uid 2000). Sudo is scoped to /usr/bin/isolate only.
RUN <<EOF
#!/bin/bash -ex
    useradd -ms /bin/bash -u 2000 cmsuser
    usermod -aG isolate cmsuser
    echo 'cmsuser ALL=(ALL) NOPASSWD: /usr/bin/isolate' >> /etc/sudoers
EOF

# Copy rekarel artifacts from the builder stage.
# rekarel: Node.js CLI script (needs nodejs at runtime — installed above via apt).
# karel: statically-linked C++ binary (no runtime deps).
COPY --from=rekarel-builder /usr/local/bin/rekarel /usr/local/bin/rekarel
COPY --from=rekarel-builder /usr/local/lib/node_modules/@rekarel /usr/local/lib/node_modules/@rekarel
COPY --from=rekarel-builder /build/bin/karel /usr/local/bin/karel

USER cmsuser
ENV LANG=C.UTF-8

RUN mkdir /home/cmsuser/src
COPY --chown=cmsuser:cmsuser install.py constraints.txt /home/cmsuser/src/

WORKDIR /home/cmsuser/src

RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py venv
ENV PATH="/home/cmsuser/cms/bin:$PATH"

COPY --chown=cmsuser:cmsuser . /home/cmsuser/src

# Install CMS without dev dependencies (no pytest, coverage, beautifulsoup4).
RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py cms

# Install the cms_rekarel Karel language plugin.
RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 \
    pip install git+https://github.com/AresLOLXD/cms_rekarel.git

# Copy config-generation script and entrypoint into the image.
COPY --chown=cmsuser:cmsuser docker/generate_config.py /home/cmsuser/generate_config.py
COPY --chown=cmsuser:cmsuser docker/entrypoint.sh /home/cmsuser/entrypoint.sh
RUN chmod +x /home/cmsuser/entrypoint.sh

# Bake dev/test configs for backward compatibility with docker-compose.dev.yml
# and docker-compose.test.yml. These are pre-generated and referenced by
# CMS_CONFIG in those compose files — the entrypoint skips generation when
# CMS_CONFIG already points to an existing file.
RUN <<EOF
#!/bin/bash -ex
    sed 's|/cmsuser:your_password_here@localhost:5432/cmsdb"|/postgres@testdb:5432/cmsdbfortesting"|' \
        ./config/cms.sample.toml >../cms/etc/cms-testdb.toml
    sed -e 's|/cmsuser:your_password_here@localhost:5432/cmsdb"|/postgres@devdb:5432/cmsdb"|' \
        -e 's/127.0.0.1/0.0.0.0/' \
        ./config/cms.sample.toml >../cms/etc/cms-devdb.toml
    sed -i 's/127.0.0.1/0.0.0.0/' ../cms/etc/cms_ranking.toml
EOF

ENTRYPOINT ["/home/cmsuser/entrypoint.sh"]
CMD ["supervisord", "--nodaemon", "-c", "/home/cmsuser/cms/etc/supervisord.conf"]
```

- [ ] **Step 3.2: Verify the Dockerfile builds the rekarel-builder stage**

```bash
docker build --target rekarel-builder -t cms-rekarel-test .
```

Expected: build completes. Verify both binaries exist:

```bash
docker run --rm cms-rekarel-test sh -c "rekarel -V && ls -lh /build/bin/karel"
```

Expected: rekarel version printed, `karel` file listed (~1-3 MB statically linked binary).

- [ ] **Step 3.3: Verify the full image builds**

```bash
docker build -t cms-prod-test .
```

Expected: build completes without errors.

- [ ] **Step 3.4: Confirm rekarel artifacts are present in runtime stage**

```bash
docker run --rm cms-prod-test bash -c "
    rekarel -V &&
    /usr/local/bin/karel --version 2>/dev/null || echo 'karel present (no --version flag)' &&
    python -c 'import karel.language; print(\"cms_rekarel plugin loaded\")'
"
```

Expected: rekarel version, karel binary confirmed present, `cms_rekarel plugin loaded`.

- [ ] **Step 3.5: Commit**

```bash
git add Dockerfile
git commit -m "feat(docker): multi-stage build with rekarel-builder stage and supervisord runtime"
```

---

## Task 4: docker/docker-compose.prod.yml

**Files:**
- Create: `docker/docker-compose.prod.yml`

- [ ] **Step 4.1: Create the production compose file**

Create `docker/docker-compose.prod.yml`:

```yaml
# Production Docker Compose for CMS.
#
# Usage:
#   With bundled PostgreSQL:   docker compose -f docker/docker-compose.prod.yml --profile localdb up -d
#   With external PostgreSQL:  docker compose -f docker/docker-compose.prod.yml up -d
#
# Requires a .env file at the repo root. Copy .env.example and fill in the values.

services:

  # ── PostgreSQL (optional — only active with --profile localdb) ────────────
  db:
    profiles: ["localdb"]
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - cms-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s

  # ── Database initialization (run once, then exits) ────────────────────────
  # This service initializes the CMS database schema. It runs cmsInitDB and
  # exits. The main cms service waits for this to complete before starting.
  # Re-running it on an existing database is safe (uses CREATE TABLE IF NOT EXISTS).
  db-init:
    build:
      context: ..
    env_file: ../.env
    depends_on:
      db:
        condition: service_healthy
        required: false
    command: ["cmsInitDB"]
    restart: "no"

  # ── CMS (all services managed by supervisord) ─────────────────────────────
  cms:
    build:
      context: ..
    env_file: ../.env
    depends_on:
      db-init:
        condition: service_completed_successfully
    volumes:
      - cms-data:/home/cmsuser/cms/lib
      - cms-logs:/home/cmsuser/cms/log
      - cms-cache:/home/cmsuser/cms/cache
    tmpfs:
      - /home/cmsuser/cms/run
    privileged: true
    cgroup: host
    ports:
      - "${CMS_CWS_HTTP_PORT:-8888}:${CMS_CWS_HTTP_PORT:-8888}"
      - "${CMS_AWS_HTTP_PORT:-8889}:${CMS_AWS_HTTP_PORT:-8889}"
      - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:${CMS_AWS_HTTP_PORT:-8889}/ || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 60s
    restart: unless-stopped

volumes:
  cms-pgdata:
  cms-data:
  cms-logs:
  cms-cache:
```

- [ ] **Step 4.2: Validate the compose file syntax**

```bash
docker compose -f docker/docker-compose.prod.yml config --quiet && echo "OK"
```

Expected: `OK` (no syntax errors).

- [ ] **Step 4.3: Commit**

```bash
git add docker/docker-compose.prod.yml
git commit -m "feat(docker): add docker-compose.prod.yml for production deployment"
```

---

## Task 5: End-to-end smoke test

- [ ] **Step 5.1: Create a minimal .env for testing**

```bash
SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
cat > /tmp/cms-smoke.env <<EOF
CMS_DB_URL=postgresql+psycopg2://cms:testpass@db:5432/cmsdb
CMS_SECRET_KEY=${SECRET}
CMS_CONTEST_ID=1
POSTGRES_USER=cms
POSTGRES_PASSWORD=testpass
POSTGRES_DB=cmsdb
CMS_RWS_PASSWORD=rwspass
EOF
```

- [ ] **Step 5.2: Verify the entrypoint generates valid config from env vars**

```bash
docker run --rm \
  --env-file /tmp/cms-smoke.env \
  -e CMS_CONFIG=/tmp/nonexistent-to-force-generation.toml \
  cms-prod-test \
  bash -c "
    python3 /home/cmsuser/generate_config.py
    python3 -c 'import cms.conf; print(\"Config valid\")'
  "
```

Expected: `Generated /home/cmsuser/cms/etc/cms.toml`, `Generated /home/cmsuser/cms/etc/cms_ranking.toml`, `Generated /home/cmsuser/cms/etc/supervisord.conf`, `Config valid`.

- [ ] **Step 5.3: Verify the entrypoint rejects the insecure default secret key**

```bash
docker run --rm \
  -e CMS_DB_URL="postgresql+psycopg2://x:y@z/db" \
  -e CMS_SECRET_KEY="8e045a51e4b102ea803c06f92841a1fb" \
  -e CMS_CONTEST_ID=1 \
  cms-prod-test \
  bash -c "python3 /home/cmsuser/generate_config.py; echo 'Should not reach here'"
```

Expected: exits with non-zero code and prints `ERROR: CMS_SECRET_KEY is set to the public example value.`

- [ ] **Step 5.4: Verify the existing test compose still builds**

```bash
docker compose -p cms -f docker/docker-compose.test.yml build testcms
```

Expected: build completes.

- [ ] **Step 5.5: Final commit**

```bash
git add docs/superpowers/plans/2026-05-07-docker-production.md
git commit -m "docs: add Docker production deployment implementation plan"
```
