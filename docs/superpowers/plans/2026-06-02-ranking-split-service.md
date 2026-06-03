# Ranking Split Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `cmsRankingWebServer` into its own lightweight Docker service so ranking-only code changes rebuild in seconds, and fix the `_do_up()` UX to remember the localdb choice and offer a 4-option rebuild menu.

**Architecture:** A new `Dockerfile.ranking` produces a Python-only image (no compilers, isolate, rekarel). `generate_config.py` gains a `CMS_RANKING_ONLY` mode that skips `cms.toml` and supervisord generation. The `cms` service drops the ranking program from its supervisord config and routes ranking pushes to the `ranking` service hostname via `CMS_RWS_HOST`.

**Tech Stack:** bash, Python 3, Docker Compose, pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docker/generate_config.py` | Modify | Add `CMS_RWS_HOST` env var; add `CMS_RANKING_ONLY` skip mode; remove `cmsrankingwebserver` from supervisord |
| `docker/test_generate_config.py` | Modify | Cover new `CMS_RWS_HOST` and `CMS_RANKING_ONLY` behaviour |
| `docker/_lib.sh` | Modify | Fix localdb default; replace rebuild yes/no with 4-option menu |
| `docker/test_lib.sh` | Modify | Cover localdb default reading |
| `Dockerfile.ranking` | Create | Lightweight ranking-only image (Python only, no compilers) |
| `docker/entrypoint_ranking.sh` | Create | Minimal entrypoint: generate ranking config then exec |
| `docker/docker-compose.prod.yml` | Modify | Add `ranking` service; move RWS port; inject `CMS_RWS_HOST=ranking` into `cms` |
| `.env.example` | Modify | Document `CMS_RWS_HOST` |

---

### Task 1: Update `generate_config.py` — `CMS_RWS_HOST` + `CMS_RANKING_ONLY` + remove ranking from supervisord

**Files:**
- Modify: `docker/generate_config.py`
- Modify: `docker/test_generate_config.py`

- [ ] **Step 1: Write failing tests**

Add the following four tests at the end of `docker/test_generate_config.py`:

```python
def test_cms_toml_proxy_uses_default_localhost_rws_host(monkeypatch):
    _set(monkeypatch)
    toml = gc.generate_cms_toml()
    assert "@localhost:8890/" in toml


def test_cms_toml_proxy_uses_custom_rws_host(monkeypatch):
    _set(monkeypatch, {"CMS_RWS_HOST": "ranking"})
    toml = gc.generate_cms_toml()
    assert "@ranking:8890/" in toml
    assert "@localhost" not in toml


def test_supervisord_no_ranking_webserver(monkeypatch):
    _set(monkeypatch)
    conf = gc.generate_supervisord_conf()
    assert "cmsRankingWebServer" not in conf
    assert "cmsrankingwebserver" not in conf


def test_ranking_only_mode_skips_cms_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("CMS_RANKING_ONLY", "true")
    monkeypatch.setenv("CMS_RANKING_CONFIG", str(tmp_path / "ranking.toml"))
    # Must NOT call validate_required() — so DB_URL / SECRET_KEY are absent
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.delenv("CMS_SECRET_KEY", raising=False)
    gc.main()  # must not SystemExit
    assert (tmp_path / "ranking.toml").exists()
    assert "http_port" in (tmp_path / "ranking.toml").read_text()


def test_ranking_only_mode_skips_supervisord(monkeypatch, tmp_path):
    monkeypatch.setenv("CMS_RANKING_ONLY", "true")
    monkeypatch.setenv("CMS_CONTEST_ID", "1")
    monkeypatch.setenv("CMS_RANKING_CONFIG", str(tmp_path / "ranking.toml"))
    monkeypatch.delenv("CMS_DB_URL", raising=False)
    monkeypatch.delenv("CMS_SECRET_KEY", raising=False)
    gc.main()
    # supervisord.conf must NOT be created (no write to /home/cmsuser/… paths attempted)
    assert not (tmp_path / "supervisord.conf").exists()
```

- [ ] **Step 2: Run failing tests to confirm they fail**

```bash
.venv/bin/pytest docker/test_generate_config.py::test_cms_toml_proxy_uses_default_localhost_rws_host \
    docker/test_generate_config.py::test_cms_toml_proxy_uses_custom_rws_host \
    docker/test_generate_config.py::test_supervisord_no_ranking_webserver \
    docker/test_generate_config.py::test_ranking_only_mode_skips_cms_toml \
    docker/test_generate_config.py::test_ranking_only_mode_skips_supervisord \
    -v
```

Expected: all 5 FAIL.

- [ ] **Step 3: Add `CMS_RWS_HOST` to `generate_cms_toml()`**

In `docker/generate_config.py`, after line 81 (`rws_password = _get(...)`), add:

```python
    rws_host = _get("CMS_RWS_HOST", "localhost")
```

Change line 133 from:
```python
rankings = ["http://{_url_quote(rws_username, safe="")}:{_url_quote(rws_password, safe="")}@localhost:{rws_http_port}/"]
```
to:
```python
rankings = ["http://{_url_quote(rws_username, safe="")}:{_url_quote(rws_password, safe="")}@{rws_host}:{rws_http_port}/"]
```

- [ ] **Step 4: Remove `cmsrankingwebserver` from `generate_supervisord_conf()`**

Delete line 210 from `docker/generate_config.py`:
```python
    blocks.append(program("cmsrankingwebserver", "cmsRankingWebServer", 55))
```

- [ ] **Step 5: Add `CMS_RANKING_ONLY` mode to `main()`**

Replace the existing `main()` function body in `docker/generate_config.py` with:

```python
def main() -> None:
    ranking_only = os.environ.get("CMS_RANKING_ONLY", "").lower() == "true"
    ranking_config_path = _get("CMS_RANKING_CONFIG", "/home/cmsuser/cms/etc/cms_ranking.toml")
    supervisord_path = "/home/cmsuser/cms/etc/supervisord.conf"

    if not ranking_only:
        explicit_config = os.environ.get("CMS_CONFIG")
        config_path = explicit_config or "/home/cmsuser/cms/etc/cms.toml"
        if explicit_config and os.path.isfile(config_path):
            print(f"Using existing config: {config_path}", file=sys.stderr)
        else:
            validate_required()
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(generate_cms_toml())
            print(f"Generated {config_path}", file=sys.stderr)

    os.makedirs(os.path.dirname(ranking_config_path), exist_ok=True)
    with open(ranking_config_path, "w") as f:
        f.write(generate_cms_ranking_toml())
    print(f"Generated {ranking_config_path}", file=sys.stderr)

    if not ranking_only and os.environ.get("CMS_CONTEST_ID"):
        os.makedirs(os.path.dirname(supervisord_path), exist_ok=True)
        with open(supervisord_path, "w") as f:
            f.write(generate_supervisord_conf())
        print(f"Generated {supervisord_path}", file=sys.stderr)
    elif not ranking_only:
        print("CMS_CONTEST_ID not set — skipping supervisord.conf generation.", file=sys.stderr)
```

- [ ] **Step 6: Run all tests to verify they pass**

```bash
.venv/bin/pytest docker/test_generate_config.py -v
```

Expected: all tests PASS (including the 5 new ones).

- [ ] **Step 7: Commit**

```bash
git add docker/generate_config.py docker/test_generate_config.py
git commit -m "feat: add CMS_RWS_HOST, CMS_RANKING_ONLY mode, remove ranking from supervisord"
```

---

### Task 2: Create `docker/entrypoint_ranking.sh`

**Files:**
- Create: `docker/entrypoint_ranking.sh`

- [ ] **Step 1: Create the file**

```bash
#!/bin/bash
set -euo pipefail

python3 /home/cmsuser/generate_config.py

rm -f /home/cmsuser/cms/run/*.sock 2>/dev/null || true

exec "$@"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x docker/entrypoint_ranking.sh
```

- [ ] **Step 3: Commit**

```bash
git add docker/entrypoint_ranking.sh
git commit -m "feat: add entrypoint_ranking.sh for ranking-only container"
```

---

### Task 3: Fix `_do_up()` in `docker/_lib.sh`

**Files:**
- Modify: `docker/_lib.sh`
- Modify: `docker/test_lib.sh`

- [ ] **Step 1: Add tests to `docker/test_lib.sh`**

Add after the existing `COMPOSE_CMD` test block, before the `Summary` section:

```bash
# ── localdb default ───────────────────────────────────────────────────────────

result=$(CMS_USE_LOCALDB=true bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "true" ]]; then
  check "_env_var CMS_USE_LOCALDB returns 'true' when set in env" "ok"
else
  check "_env_var CMS_USE_LOCALDB returns 'true' when set in env" "got '$result'"
fi

result=$(CMS_USE_LOCALDB=false bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "false" ]]; then
  check "_env_var CMS_USE_LOCALDB returns 'false' when set to false" "ok"
else
  check "_env_var CMS_USE_LOCALDB returns 'false' when set to false" "got '$result'"
fi

result=$(CMS_USE_LOCALDB="" bash -c "source '$REPO_ROOT/docker/_lib.sh'; _env_var CMS_USE_LOCALDB false")
if [[ "$result" == "false" ]]; then
  check "_env_var CMS_USE_LOCALDB falls back to default when unset" "ok"
else
  check "_env_var CMS_USE_LOCALDB falls back to default when unset" "got '$result'"
fi
```

- [ ] **Step 2: Run the new tests to confirm they pass already (they test existing `_env_var` behavior)**

```bash
bash docker/test_lib.sh
```

Expected: Results: N passed, 0 failed (new tests pass because `_env_var` already works correctly — the fix is in how `_do_up()` uses it).

- [ ] **Step 3: Replace `_do_up()` in `docker/_lib.sh`**

Replace the entire `_do_up()` function (lines 62–85) with:

```bash
# _do_up — Docker preflight, localdb choice (persisted), selective rebuild, compose up --wait
_do_up() {
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running or not accessible." >&2
    exit 1
  fi

  local up_cmd=(docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" -p "$PROJECT_NAME")

  local current_localdb
  current_localdb="$(_env_var CMS_USE_LOCALDB false)"
  local localdb_default
  [[ "$current_localdb" == "true" ]] && localdb_default="y" || localdb_default="n"

  if ask_yes_no "Use local PostgreSQL container?" "$localdb_default"; then
    _set_env_var "CMS_USE_LOCALDB" "true"
    up_cmd+=(--profile localdb)
    COMPOSE_CMD+=(--profile localdb)
  else
    _set_env_var "CMS_USE_LOCALDB" "false"
  fi

  local choice
  printf "Rebuild?\n  1) No  (default)\n  2) All services\n  3) Ranking only\n  4) CMS only\n"
  read -r -p "Choice [1-4]: " choice
  choice="${choice:-1}"

  case "$choice" in
    2) "${up_cmd[@]}" build ;;
    3) "${up_cmd[@]}" build ranking ;;
    4) "${up_cmd[@]}" build cms db-init ;;
  esac

  "${up_cmd[@]}" up -d --wait --wait-timeout 90
}
```

- [ ] **Step 4: Run the full test suite**

```bash
bash docker/test_lib.sh
```

Expected: Results: N passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add docker/_lib.sh docker/test_lib.sh
git commit -m "fix: read CMS_USE_LOCALDB default from .env; add 4-option rebuild menu to _do_up()"
```

---

### Task 4: Create `Dockerfile.ranking`

**Files:**
- Create: `Dockerfile.ranking`

- [ ] **Step 1: Create `Dockerfile.ranking` at the repo root**

```dockerfile
# syntax=docker/dockerfile:1
# Lightweight image for cmsRankingWebServer only.
# No compilers, no isolate, no rekarel — Python runtime only.
ARG BASE_IMAGE=ubuntu:noble
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y \
        libffi-dev \
        libpq-dev \
        libyaml-dev \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv
EOF

RUN useradd -ms /bin/bash -u 2000 cmsuser

USER cmsuser
ENV LANG=C.UTF-8

RUN mkdir /home/cmsuser/src
COPY --chown=cmsuser:cmsuser install.py constraints.txt /home/cmsuser/src/

WORKDIR /home/cmsuser/src

RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py venv
ENV PATH="/home/cmsuser/cms/bin:$PATH"

COPY --chown=cmsuser:cmsuser . /home/cmsuser/src

RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py --skip-isolate cms

COPY --chown=cmsuser:cmsuser docker/generate_config.py /home/cmsuser/generate_config.py
COPY --chown=cmsuser:cmsuser docker/entrypoint_ranking.sh /home/cmsuser/entrypoint.sh
RUN chmod +x /home/cmsuser/entrypoint.sh

ENV CMS_RANKING_ONLY=true

ENTRYPOINT ["/home/cmsuser/entrypoint.sh"]
CMD ["cmsRankingWebServer"]
```

- [ ] **Step 2: Verify the image builds**

```bash
docker build -f Dockerfile.ranking -t cms-ranking-test .
```

Expected: build completes without errors.

- [ ] **Step 3: Verify `cmsRankingWebServer` is present in the image**

```bash
docker run --rm cms-ranking-test which cmsRankingWebServer
```

Expected: prints `/home/cmsuser/cms/bin/cmsRankingWebServer` (or similar path).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.ranking
git commit -m "feat: add Dockerfile.ranking — lightweight ranking-only image"
```

---

### Task 5: Update `docker-compose.prod.yml` and `.env.example`

**Files:**
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add `ranking` service and update `cms` service in `docker-compose.prod.yml`**

In the `cms` service, **remove** the RWS port mapping:
```yaml
      - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
```

In the `cms` service, add `CMS_RWS_HOST` to the environment so `generate_config.py` routes ranking pushes to the `ranking` container. After `env_file:`, add:
```yaml
    environment:
      CMS_RWS_HOST: ranking
```

Add the `ranking` service after the `cms` service block, before `volumes:`:

```yaml
  # ── Ranking web server (independent — lightweight Python-only image) ───────
  ranking:
    build:
      context: ..
      dockerfile: Dockerfile.ranking
    env_file:
      - path: ../.env
        required: false
    volumes:
      - cms-data:/home/cmsuser/cms/lib
    ports:
      - "${CMS_RWS_HTTP_PORT:-8890}:${CMS_RWS_HTTP_PORT:-8890}"
    healthcheck:
      test: ["CMD-SHELL", "curl -sfL http://localhost:${CMS_RWS_HTTP_PORT:-8890}/ || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "${CMS_LOG_MAX_SIZE:-500m}"
        max-file: "${CMS_LOG_MAX_FILES:-10}"
```

- [ ] **Step 2: Verify the compose config parses**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env config --quiet
```

Expected: exits 0 with no errors. (If `.env` doesn't exist, copy `.env.example` first.)

- [ ] **Step 3: Add `CMS_RWS_HOST` to `.env.example`**

In `.env.example`, after the `CMS_RWS_PASSWORD=CHANGE_ME` line (line 165), add:

```
# Hostname of the ranking web server as seen from the CMS container.
# Set to "ranking" when using Docker Compose (the service name resolves on the
# internal network). Leave blank or set to "localhost" for non-Docker installs.
# CMS_RWS_HOST=ranking
```

- [ ] **Step 4: Run existing tests to ensure nothing regressed**

```bash
.venv/bin/pytest docker/test_generate_config.py -v
bash docker/test_lib.sh
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add docker/docker-compose.prod.yml .env.example
git commit -m "feat: add ranking Docker service; move RWS port; document CMS_RWS_HOST"
```
