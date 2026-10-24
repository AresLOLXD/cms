# Retire CMS-Loader and refresh the Docker image (issues #18 and #26) — Design

**Date:** 2026-10-24
**Status:** Decisions taken by a read-only specialist panel (docker-expert,
security-engineer) and verified against main `10121ec8`; approved by the
owner as a consolidated design on 2026-10-24. One lane because both issues
edit the same files (`Dockerfile`, `docker/docker-compose*.yml`,
`.env.example`, `docker/generate_config.py` + test, docs).

## Intent

AWS imports users and participations from CSV (Phase A, on main), so
CMS-Loader (Phase B of `2026-09-29-aws-bulk-import-design.md`) is retired.
At the same time the image gets the maintenance items of #26.

## Already done on main (do NOT redo)

#26 item N9-D (publish AWS and other admin ports on loopback only): commit
`b6642e13` publishes every port as `127.0.0.1:...` and
`test_prod_compose_publishes_ports_on_localhost_only` asserts it. The
reverse proxy on the host reaches `127.0.0.1:8889`. No `.env` knob for the
bind address is built. The only remaining change for that item is the doc
sentence of step 4 (Commit 6).

## Non-goals

- A start-time isolate warning, a CWS port shifter, a `-dirty` suffix on the
  revision, CI plumbing for `GIT_SHA`, a hard failure on leftover
  `CMS_LOADER_*`, an `ARG NODE_MAJOR` shared across stages, a pin for
  `cms_rekarel`.
- The other exposure notes (dev compose publishing all interfaces, default
  placeholder secrets, `privileged`): list them in the final report, do not
  fix them.

## Commits, in this order

### 1. `refactor(docker)!: retire CMS-Loader`
- `Dockerfile`: remove the `loader-builder` stage and the 5
  `COPY --from=loader-builder` lines.
- Both compose files that mention it (`docker-compose.prod.yml`: build args
  `CMS_LOADER_VERSION`, `CMS_LOADER_BASE_PATH` in `db-init` and `cms`; the
  `9995` port line). Dev and test compose have no loader args.
- `docker/generate_config.py`: the `cmsloader` supervisord block; remove the
  loader tests from `docker/test_generate_config.py`; fix
  `test_supervisord_every_program_stops_as_a_group_and_retries` (sets
  `CMS_LOADER_*` and expects `cmsloader` in its program set).
- `.env.example`: the `CMS_LOADER_*` variables and port (about lines 298-322).
- Docs: delete `docs/cms-loader.md` and `docs/locale/es/LC_MESSAGES/cms-loader.po`;
  its `docs/index.rst` toctree entry; the README table row; the 9995 row in
  `docs/docker-deployment.md` (about line 95); the sentence in
  `docs/importing-users.md`; the same edits in the `.po` of
  `docker-deployment` and `importing-users`; `docs/locale/es/GLOSSARY.md`;
  point to the AWS page from a short "Importing users" section. The docs CI
  runs `sphinx-build -W`: any dangling `cms-loader` link fails it.
- Leftover `CMS_LOADER_*` in an operator's `.env`: **ignored** by compose and
  `generate_config.py` (`env_file` passes unknown variables harmlessly), plus
  a 3-line warning in `_do_up` of `docker/_lib.sh` when
  `_env_var CMS_LOADER_SESSION_SECRET ""` is non-empty: CMS-Loader was
  removed, the `CMS_LOADER_*` variables are ignored, import users from AWS
  (`docs/importing-users.md`). Never fail the deploy.
- Ops note for the final report (not a repo change): the host Caddy route to
  9995 will answer 502 after the rebuild.
- Check `docker/test_lib.sh` and `docker/test_contest_env.sh` still pass.

### 2. `fix(docker): refuse a CWS port range that overlaps AWS or RWS`
In `generate_cms_toml()` right after the port reads, `sys.exit(1)` with an
ERROR line like the neighbouring checks when
`range(cws_http_port, cws_http_port + cws_count)` contains the AWS or RWS
port. Refuse, do not shift (shifting would desync from compose published
ports, the healthcheck that uses `CMS_AWS_HTTP_PORT`, and Caddy). Do not
include the loader port (moot). Parametrized test: AWS inside the range, RWS
inside the range, no overlap passes. Do not duplicate the check in `_lib.sh`.

### 3. `build(docker): Node 24 and an isolate >= 2.6 build check`
- After commit 1 only `rekarel-builder` and the runtime stage need Node.
  Change `setup_20.x` to `setup_24.x` in both (Node 22 enters maintenance in
  October 2026). `@rekarel/cli` is pure JS; no known incompatible
  dependency. Cheap local check: `node:24` official image, `npm i -g
  @rekarel/cli && rekarel --version`. If it fails, use 22 and say so.
- isolate: in the same `RUN` as `apt-get install -y isolate` add
  `dpkg --compare-versions "$(dpkg-query -W -f='${Version}' isolate)" ge 2.6
  || { echo ...; exit 1; }` (use `dpkg-query`, not `isolate --version`).
  Same `RUN` as `apt-get update` so a cached layer only exists if it passed.
  Optional static test that the Dockerfile contains the check (pattern: the
  existing `test_dockerfile_creates_the_data_dir_as_cmsuser`, which skips
  when the Dockerfile is absent).

### 4. `build(docker): pin @rekarel/cli`
Needs the version that runs in production. **The owner runs
`docker compose exec cms rekarel --version` on the server and gives the
output; do not connect to any server.** Until then, leave this commit out and
report it as pending. Then `npm install -g @rekarel/cli@<version>` and update
the "latest" lines in `docs/rekarel.md` (about 42-52) and its `.po`.

### 5. `build(docker): add the OCI revision label from GIT_SHA`
`ARG GIT_SHA=unknown` and `LABEL org.opencontainers.image.revision=$GIT_SHA`
at the very end of the last stage of `Dockerfile` and of
`docker/Dockerfile.ranking` (end of stage, so the layer cache is not
invalidated). Compose: `GIT_SHA: ${GIT_SHA:-unknown}` in the build args of
`cms`, `db-init` and `ranking`. `_do_up` in `docker/_lib.sh`:
`export GIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo
unknown)"`. `cmstestsuite/loadtest/build.sh` uses `git archive` (no `.git`):
skip it. A direct `docker compose build` yields `unknown`: acceptable.

### 6. `docs(docker): AWS is reached through the proxy or an SSH tunnel`
`docs/docker-deployment.md` step 4 still tells the operator to open
`http://your-server:8889`; with the loopback bind only the reverse proxy or
`ssh -L 8889:127.0.0.1:8889` works. Fix that sentence (and its `.po`).

## Verification

Verifiable locally: `pytest docker/test_generate_config.py` (one file per
pytest run), `docker/test_lib.sh`, `docker/test_contest_env.sh`, a bash run
of the `_do_up` warning with a fake `.env`, `docker compose -f
docker/docker-compose.prod.yml config`, the docs check (`sphinx-build -W`
en and es, `docs/tests`, gettext + `docs/check_translations.py`).
**Needs a cold build (~55 min no-cache) and must be reported as NOT
verified:** Node 24 through NodeSource on both bases (noble, bookworm), the
isolate `dpkg` check passing in the real image, the OCI label on the built
image (`docker inspect`). Do not start a cold build.

## Out of scope for this branch

Another lane (issue #17) and another (issue #19) edit
`docs/importing-users.md` and its `.po`; keep your edits there to the single
loader sentence.

## Tasks

The six commits above, in order; commit 4 only when the version is known.
