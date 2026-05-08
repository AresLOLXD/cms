# Docker Log Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Docker `json-file` log rotation to the production compose file with configurable defaults (500 MB × 10 files = 5 GB), exposed as `CMS_LOG_MAX_SIZE` and `CMS_LOG_MAX_FILES` env vars.

**Architecture:** Two-file change. `docker-compose.prod.yml` gets a `logging:` block on the `cms` service that reads from env vars with defaults. `.env.example` gets a new `LOGGING` section documenting the vars. No application code changes.

**Tech Stack:** Docker Compose v2, `json-file` log driver

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docker/docker-compose.prod.yml` | Modify | Add `logging:` block with env-var-driven size/count |
| `.env.example` | Modify | Document `CMS_LOG_MAX_SIZE` and `CMS_LOG_MAX_FILES` |

---

### Task 1: Add `logging:` block to `docker-compose.prod.yml`

**Agent:** `voltagent-infra:docker-expert`

**Files:**
- Modify: `docker/docker-compose.prod.yml`

The `cms` service currently has no `logging:` key. Add it after the `restart:` line.

- [ ] **Step 1: Read the current `cms` service block**

  Read `docker/docker-compose.prod.yml` and locate the `cms:` service. Confirm there is no existing `logging:` key.

- [ ] **Step 2: Add the `logging:` block**

  In `docker/docker-compose.prod.yml`, inside the `cms:` service, add the following block directly after the `restart: unless-stopped` line:

  ```yaml
      logging:
        driver: "json-file"
        options:
          max-size: "${CMS_LOG_MAX_SIZE:-500m}"
          max-file: "${CMS_LOG_MAX_FILES:-10}"
  ```

  The indentation must match the `restart:` key (4 spaces). The full `cms` service tail should look like:

  ```yaml
      restart: unless-stopped
      logging:
        driver: "json-file"
        options:
          max-size: "${CMS_LOG_MAX_SIZE:-500m}"
          max-file: "${CMS_LOG_MAX_FILES:-10}"
  ```

- [ ] **Step 3: Validate the compose file parses**

  ```bash
  cd /var/home/areslolxd/Documentos/cms
  docker compose -f docker/docker-compose.prod.yml config --quiet
  ```

  Expected: no output, exit code 0. If it prints errors, fix indentation.

- [ ] **Step 4: Verify defaults appear correctly in resolved config**

  ```bash
  docker compose -f docker/docker-compose.prod.yml config | grep -A4 "logging"
  ```

  Expected output:
  ```yaml
      logging:
        driver: json-file
        options:
          max-file: "10"
          max-size: 500m
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add docker/docker-compose.prod.yml
  git commit -m "feat(docker): add json-file log rotation with 500m x 10 defaults"
  ```

---

### Task 2: Document logging vars in `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add the LOGGING section**

  Open `.env.example` and append the following block at the very end (after the `TELEGRAM BOT` section):

  ```ini
  # -----------------------------------------------------------
  # LOGGING (optional — defaults shown)
  # -----------------------------------------------------------

  # Maximum size of each Docker log file before rotation.
  # Use Docker size units: 500m = 500 MB, 1g = 1 GB.
  CMS_LOG_MAX_SIZE=500m

  # Number of rotated log files to keep (total = MAX_SIZE × MAX_FILES).
  # Default: 10 files × 500m = 5 GB max on disk.
  CMS_LOG_MAX_FILES=10
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add .env.example
  git commit -m "docs(docker): document CMS_LOG_MAX_SIZE and CMS_LOG_MAX_FILES in .env.example"
  ```

---

### Task 3: Verify override works

- [ ] **Step 1: Confirm env var override is respected**

  ```bash
  cd /var/home/areslolxd/Documentos/cms
  CMS_LOG_MAX_SIZE=100m CMS_LOG_MAX_FILES=2 \
    docker compose -f docker/docker-compose.prod.yml config | grep -A4 "logging"
  ```

  Expected:
  ```yaml
      logging:
        driver: json-file
        options:
          max-file: "2"
          max-size: 100m
  ```

- [ ] **Step 2: Confirm defaults apply when vars are absent**

  ```bash
  docker compose -f docker/docker-compose.prod.yml config | grep -A4 "logging"
  ```

  Expected:
  ```yaml
      logging:
        driver: json-file
        options:
          max-file: "10"
          max-size: 500m
  ```
