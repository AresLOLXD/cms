# Ranking Dockerfile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a minimal `docker/Dockerfile.ranking` so the `ranking` service builds a lean image (~400–500 MB) instead of reusing the heavy `cms` image (~2 GB).

**Architecture:** New `docker/Dockerfile.ranking` installs only Python 3 + the CMS Python package (no language compilers, no Node.js, no Karel, no isolate, no supervisor). The `ranking` service in `docker-compose.prod.yml` is updated to reference this Dockerfile and drops the now-irrelevant build args.

**Tech Stack:** Docker multi-layer build, Python 3 venv, Ubuntu Noble base.

---

### Task 1: Create `docker/Dockerfile.ranking`

**Files:**
- Create: `docker/Dockerfile.ranking`

- [ ] **Step 1: Create the file**

```dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ubuntu:noble
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked <<EOF
#!/bin/bash -ex
    export DEBIAN_FRONTEND=noninteractive
    rm -f /etc/apt/apt.conf.d/docker-clean
    apt-get update
    apt-get install -y \
        build-essential \
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

RUN --mount=type=cache,target=/home/cmsuser/.cache/pip,uid=2000 ./install.py cms --skip-isolate

COPY --chown=cmsuser:cmsuser docker/generate_config.py /home/cmsuser/generate_config.py
COPY --chown=cmsuser:cmsuser docker/entrypoint.sh /home/cmsuser/entrypoint.sh
RUN chmod +x /home/cmsuser/entrypoint.sh

ENTRYPOINT ["/home/cmsuser/entrypoint.sh"]
CMD ["cmsRankingWebServer", "0"]
```

Note: `--skip-isolate` is required because `install.py cms` checks for `isolate` by
default — the ranking container does not install isolate.

The two-step pip install (`./install.py venv` then `./install.py cms`) mirrors the
main Dockerfile so Docker can cache the dependency layer separately from source changes.

- [ ] **Step 2: Build the image locally to verify it compiles**

Run from the repo root:
```bash
docker build -f docker/Dockerfile.ranking -t cms-ranking-test .
```
Expected: build completes without errors. The final layer should print something like
`Installing CMS package`.

- [ ] **Step 3: Confirm image size is significantly smaller than the cms image**

```bash
docker images | grep -E "cms-ranking-test|IMAGE"
```
Expected: `cms-ranking-test` is under 600 MB. Compare against the `cms` image if built.

- [ ] **Step 4: Commit**

```bash
git add docker/Dockerfile.ranking
git commit -m "feat(docker): add minimal Dockerfile.ranking for ranking service"
```

---

### Task 2: Update `docker-compose.prod.yml` — ranking build section

**Files:**
- Modify: `docker/docker-compose.prod.yml`

- [ ] **Step 1: Replace the `build` block of the `ranking` service**

Current:
```yaml
  ranking:
    build:
      context: ..
      args:
        CMS_LOADER_VERSION: ${CMS_LOADER_VERSION:-main}
        CMS_LOADER_BASE_PATH: ${CMS_LOADER_BASE_PATH:-/}
```

Replace with:
```yaml
  ranking:
    build:
      context: ..
      dockerfile: docker/Dockerfile.ranking
```

The `CMS_LOADER_VERSION` and `CMS_LOADER_BASE_PATH` args are removed because
`Dockerfile.ranking` has no loader-builder stage.

- [ ] **Step 2: Verify the compose file is valid**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env config --quiet
```
Expected: exits 0 with no output (no parse errors).

- [ ] **Step 3: Build the ranking service through compose to confirm wiring**

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env build ranking
```
Expected: build succeeds, same result as Task 1 Step 2.

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.prod.yml
git commit -m "feat(docker): point ranking service to Dockerfile.ranking"
```

---

### Task 3: Push

- [ ] **Step 1: Push both commits**

```bash
git push
```
Expected: `main -> main` with 2 new commits.
