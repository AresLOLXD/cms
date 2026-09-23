# Multi-Contest Deployment with Per-Group Rankings — Design Spec

**Date:** 2026-09-23
**Status:** Approved (design); pending written-spec review

## Problem

On exam days two contests (e.g. two olympiads, OLIM and OMIPS) run at the
same time, each with its own public scoreboard. Today this is done with **two
complete Docker stacks** on the same machine, one per contest, each with its
own Postgres, Workers, CWS, AWS and RWS (e.g. RWS on 8890 and 7890), and a
host-side reverse proxy mapping one domain to each stack.

This has three costs the operator wants to remove:

- **Duplicated resources:** two databases, two worker pools; one stack's
  workers sit idle while the other's are saturated.
- **Two admin panels:** questions, announcements and results live in two AWS
  instances and two databases.
- **Operations:** two `.env` files, two port sets, contest switching via
  `CMS_CONTEST_ID` + `./contest.sh` + restart, importing users/tasks twice.

## Goals

A single stack serves several contests at once. From the AWS, **without
restarting anything**, the operator can:

1. Mark which contests are **active** (visible to contestants in CWS).
2. Assign each contest to a **ranking group**; each group is an independent
   public scoreboard reachable at `http://<rws>/<group>/`.

Success: the operator activates contests A and B in the AWS, assigns them to
groups `olim` and `omips`, and — with no restart — contestants see A and B in
CWS, and `/olim/` and `/omips/` show two independent, live-updating
scoreboards with no score summed across them.

## Non-Goals (this sub-project)

- Ranking visibility policies (hidden, frozen, private/password, scheduled).
  → Sub-project 2.
- Per-domain CWS (making `cms.omips…` list only OMIPS contests). → Sub-project 3.
- Per-contest admin permissions (upstream issue #651).
- Multi-contest support for `cmsTelegramBot`.

## Decomposition

| # | Sub-project | Depends on |
|---|---|---|
| **1** | Multi-contest + per-group rankings (this spec) | — |
| 2 | Ranking visibility: hide, freeze at end, private staff view with password configured in AWS, automatic schedule — per group | 1 |
| 3 | Per-domain CWS (optional) | 1 |

Sub-project 1 must leave room for 2 without redesign (see "Forward
compatibility").

## Upstream Context

- Upstream issue #73 ("Allow services to handle many contests at the same
  time") is still open.
- Merged upstream (~2017): `EvaluationService` (#545) and `ContestWebServer`
  (#594/#739) run in multi-contest mode (no `-c`, or `-c ALL`). This fork
  already supports `CMS_CONTEST_ID=ALL` (`cms/util.py`).
- Not merged: #794 (ResourceService multi-contest autorestart) and **#1102**
  (ProxyService routing contests to different RWS, closed 2025-06). In #1102
  a maintainer proposed a "contest group" used as RWS URL prefix; this design
  adopts that idea with the group as a first-class DB entity.
- Currently `ProxyService.__init__(self, shard, contest_id: int)` has no
  default, so in `ALL` mode ProxyService cannot start.
- A single RWS fed with two contests shows one table with a global total.

## Chosen Approach

One RWS process hosts one isolated ranking **namespace per group** under a
URL prefix. ProxyService (running without `-c`) sends each contest's data to
the namespace of its group. Groups and contest activation are stored in the
DB and edited in the AWS; changes reach ProxyService through the existing
`reinitialize()` RPC.

Rejected alternatives:

- **One RWS container per contest, URL configured per contest** (#1102
  style): containers must be pre-provisioned; only dynamic within fixed slots.
- **Single RWS with a frontend contest filter:** RWS has no user↔contest
  membership; requires changing RWS entities, JS, ranks and events.

## Design

### 1. Data model

Schema change (see §6 for migration).

**New table `ranking_groups`** (`cms/db/rankinggroup.py`, exported from
`cms/db/__init__.py`):

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `name` | Unicode, unique, not null | URL slug; validated (§1.1) |
| `description` | Unicode, not null | Human title |

**New columns on `contests`:**

| Column | Type | Default | Meaning |
|---|---|---|---|
| `active` | Boolean, not null | `false` | Listed/served by CWS in multi-contest mode |
| `ranking_group_id` | Integer FK → `ranking_groups.id`, nullable, `ON UPDATE CASCADE ON DELETE SET NULL`, indexed | `NULL` | Ranking this contest is sent to; `NULL` = none |

Relationship `Contest.ranking_group` ↔ `RankingGroup.contests`. Several
contests may share a group (e.g. day 1 + day 2 summed in one scoreboard).

**`active` and `ranking_group_id` are independent.** `active` only controls
CWS visibility. Ranking membership depends only on `ranking_group_id`, so
deactivating a contest after the exam keeps its published ranking. To remove
a contest from a ranking, unset its group.

#### 1.1 Group name validation

A single module in `cmscommon/` (e.g. `cmscommon/ranking_groups.py`) defines:

- `GROUP_NAME_RE = ^[a-z0-9_-]+$`
- `RESERVED_GROUP_NAMES`: every first path segment the root RWS app already
  serves: `contests`, `tasks`, `users`, `teams`, `submissions`,
  `subchanges`, `faces`, `flags`, `sublist`, `history`, `scores`, `events`,
  `logo`, `config`, `lib`, `img`, `groups`, plus the names of files and
  directories in `cmsranking/static/` at the top level.
- `is_valid_group_name(name) -> bool`.

Both AWS and RWS import it; there is exactly one definition.

### 2. RankingWebServer (`cmsranking/RankingWebServer.py`)

- Extract the body of `main()` that builds stores, seeds and handlers into
  `build_ranking_app(lib_dir) -> (wsgi_app, stores)`. Behaviour for a given
  `lib_dir` is identical to today (including `seed_flags_and_teams`,
  `seed_logo`, `seed_faces`).
- **Root namespace:** the app over `config.lib_dir`, served at `/` exactly as
  today. Legacy `-c N` deployments are unaffected.
- **Group namespaces:** a dispatcher in front of the root app inspects the
  first path segment. If it is a valid, non-reserved group name:
  - An existing namespace is served by its own `build_ranking_app` instance
    over `lib_dir/groups/<group>/`, with the prefix stripped
    (`SCRIPT_NAME`/`PATH_INFO` adjusted).
  - An unknown namespace: authenticated write (`PUT`/`DELETE`) creates it;
    anything else returns 404.
  - `/<group>` without trailing slash redirects to `/<group>/` so the
    frontend's relative URLs resolve.
- On startup, every directory under `lib_dir/groups/` with a valid name is
  loaded as a namespace.
- Namespaces are fully isolated: stores, `ScoringStore`, SSE `DataWatcher`,
  logo, faces, flags and teams. Each olympiad can have its own logo.
- The frontend JS already uses relative URLs (`cmsranking/static/Config.js`);
  no JS changes are expected. The plan must verify this with a browser.
- All namespaces share the write credentials from `cms_ranking.toml`
  (`CMS_RWS_USERNAME` / `CMS_RWS_PASSWORD`).
- What bare `/` shows when only group namespaces are used (list, redirect,
  404) is decided in sub-project 2, because a public list can leak private
  rankings. In sub-project 1 `/` remains the root namespace.

### 3. ProxyService (`cms/service/ProxyService.py`)

`__init__(self, shard, contest_id: int | None = None)`.

**Legacy mode (`contest_id` given):** unchanged behaviour: one contest, sent
to the root of each configured ranking URL; groups ignored.

**Group mode (`contest_id is None`):**

- Still one `ProxyExecutor` per URL in `config.proxy_service.rankings`,
  created at startup (no executors created or destroyed at runtime).
- `ProxyOperation` gains a `group: str | None` field (`None` = root, used in
  legacy mode). `ProxyExecutor.execute()` partitions the batch by group and
  sends each partition to `{ranking}/{group}/{resource}/`.
- `initialize()` iterates over every contest with a ranking group (active or
  not) and enqueues contest/team/user/task data into that contest's group.
- `_missing_operations()`, `submission_scored`, `submission_tokened` and
  `dataset_updated` resolve the group from `submission.task.contest` (or
  `task.contest`) and drop items whose contest has no group. Existing
  hidden-participation and official-submission filters are kept.
- The service keeps the last known mapping `group -> set(contest_id)`.

**Cleanup (`RESET`):** RWS stores only merge on list `PUT`; nothing is ever
removed. On `reinitialize()` in group mode:

1. Compute the new mapping from the DB.
2. For each group that **lost** a contest, or no longer exists, enqueue a
   `RESET` operation for that group: `DELETE` on `contests/`, `users/` and
   `teams/` of the namespace. RWS stores already cascade to tasks,
   submissions and subchanges.
3. Clear `scores_sent_to_rankings` / `tokens_sent_to_rankings` for affected
   contests, then enqueue the full data again (as `initialize()` does).

Within one executor batch, `RESET`s are applied before `PUT`s. Ordinary
edits (task renamed, user added, contest added to a group) only merge and
never empty the scoreboard. A composition change briefly empties the
affected scoreboard; this happens during setup, not during an exam.

The `RESET` operation needs a small helper next to `safe_put_data` that
issues an authenticated `DELETE` with the same error handling
(`CannotSendError`, retry after `FAILURE_WAIT`).

### 4. AdminWebServer

- New `cms/server/admin/handlers/rankinggroup.py` with list / add / edit /
  delete handlers, templates, routes and a sidebar link. Name validated with
  `cmscommon`'s `is_valid_group_name`; duplicates rejected. Every
  create/edit/delete calls `self.service.proxy_service.reinitialize()`.
- Contest edit page (`handlers/contest.py`, template): an **Active**
  checkbox and a **Ranking group** select (with "none"). The existing save
  path already calls `reinitialize()`.
- Contest list page: show active flag and group (read-only) so the operator
  sees the exam-day setup at a glance.

### 5. ContestWebServer

Only in multi-contest mode (`contest_id is None`):

- `ContestListHandler` (`cms/server/contest/handlers/base.py`) lists only
  contests with `active = true`.
- `choose_contest` (`cms/server/contest/handlers/contest.py`) returns 404 for
  an inactive contest, exactly like an unknown name.

With `-c N` the `active` flag is ignored.

### 6. Database migration (automatic)

- New `cmscontrib/updaters/update_fork_multi_contest.sql`, idempotent:
  `ALTER TABLE contests ADD COLUMN IF NOT EXISTS active …`,
  `ADD COLUMN IF NOT EXISTS ranking_group_id …`, the FK constraint and index
  guarded by existence checks. Kept separate from upstream's
  `update_from_1.5.sql` to avoid conflicts in `sync-upstream`.
- `cmscontrib/SetupDB.py::setup_db()` applies it right after `init_db()`
  (which already creates `ranking_groups` via `create_all`, so the FK target
  exists). Upgrading a Docker deployment is therefore `pull` + `up`; the
  `db-init` container runs `cmsSetupDB`.
- Existing contests get `active = false` and no group. In `ALL` mode the
  operator must activate them; `-c N` deployments are unaffected.
- `cmstestsuite/unit_tests/schema_diff_test.py` is adjusted to apply the
  fork SQL after `update_from_1.5.sql`, so the "fresh install == upgraded
  install" check still holds.
- **Dumps:** the plan must verify that `cmsDumpImporter` accepts dumps
  exported before this change (missing `active` / `ranking_group`) and fills
  defaults. If it does not, add a fork updater **without** bumping upstream's
  DB `VERSION`. Migration step 3 (§8) depends on this.

### 7. Docker

- `docker/generate_config.py` already launches ProxyService without `-c`
  when `CMS_CONTEST_ID=ALL`; with §3 it now starts correctly.
- `docker/docker-compose.prod.yml`: add a named volume for the ranking
  container's `lib_dir` so per-group logos/faces/flags survive restarts
  (ranking data itself is regenerated by ProxyService).
- In `ALL` mode `cmsTelegramBot` is not generated (or is documented as
  unsupported); `docker/test_generate_config.py` covers whichever is chosen.
- Reverse proxy (on the host, outside this repo) — one line per olympiad,
  configured once:

  ```nginx
  server { server_name ranking.olim.example;  location / { proxy_pass http://127.0.0.1:8890/olim/;  } }
  server { server_name ranking.omips.example; location / { proxy_pass http://127.0.0.1:8890/omips/; } }
  ```

  SSE (`/events`) needs `proxy_buffering off;` as today.

### 8. Documentation

- `docs/multi-contest.md` — daily operation: create groups, activate
  contests, assign groups, nginx per domain, worker sizing, what `-c N` vs
  `ALL` means.
- `docs/migrating-to-multi-contest.md` — from two stacks to one:
  1. **Backup:** `pg_dump` of each stack's DB; copy each RWS data directory.
  2. **Update** the stack to keep (pull + up); the schema migrates
     automatically (§6). Confirm in logs.
  3. **Consolidate** stack B into A: `cmsDumpExporter` on B →
     `cmsDumpImporter` on A. Includes how to detect username/contest-name
     collisions between the two DBs before importing.
  4. **`.env`:** `CMS_CONTEST_ID=ALL`; resize `CMS_WORKER_COUNT` (previously
     split across stacks); retire stack B's ports (e.g. 7890).
  5. **AWS:** create groups (`olim`, `omips`), mark contests active, assign
     groups.
  6. **nginx:** each ranking domain → `proxy_pass …:8890/<group>/;`; CWS
     domains → the single CWS (contests at `/<contest_name>/`).
  7. **Verify & roll back:** checks per ranking and CWS; how to bring stack
     B back from the backups if something fails.
- README and `.env.example`: mention `ALL` mode + groups and link the docs.

### Forward compatibility (sub-project 2)

- `ranking_groups` will gain visibility columns (mode, freeze/hide times) and
  a staff password (hashed with `cmscommon.crypto`, sent hashed by
  ProxyService, verified by RWS). The password is configured in the AWS group
  page. Write credentials stay in `.env`.
- The private live view can be a second namespace per group (e.g.
  `<group>--staff`), reusing §2 unchanged; `-` in names is already allowed.
  Sub-project 2 will reserve that suffix.

## Error Handling

- Invalid/reserved group name: rejected in AWS form with a message; RWS
  returns 404 for GET and 403 for writes.
- ProxyService cannot reach RWS: existing `CannotSendError` + retry
  behaviour, now also for `RESET`.
- Contest with a group whose namespace does not exist yet: created on first
  `PUT`.
- Group deleted in AWS: FK sets contests' group to `NULL`; `reinitialize()`
  resets the namespace, which remains empty on disk.

## Testing

Unit (pytest, `.venv/bin/pytest`):

- **RWS dispatcher** (`cmstestsuite/unit_tests/cmsranking/`): namespace
  created by authenticated `PUT`; unauthenticated `PUT` rejected; `GET` of
  unknown namespace → 404; reserved/invalid names rejected; data and SSE
  events isolated between namespaces; namespaces reloaded from disk;
  root namespace unchanged; `/<group>` redirects to `/<group>/`.
- **Group name validation** (`cmscommon`).
- **ProxyService** (with `safe_put_data`/delete helper mocked): group mode
  sends to `/<group>/…`; contests without group dropped; `RESET` only when a
  group loses a contest or is deleted, never on ordinary edits; `RESET`
  precedes `PUT` in a batch; legacy `-c N` mode produces the same requests
  as before.
- **CWS:** contest list filters by `active`; inactive contest → 404;
  `-c N` ignores `active`.
- **AWS:** group CRUD validation; contest form saves `active` and group.
- **Migration:** fork SQL applied twice succeeds; `schema_diff_test` passes.
- **Dump import** of a pre-change dump succeeds (§6).
- **Docker:** `docker/test_generate_config.py` for any generator change.

Manual end-to-end (local Docker): two contests in groups `olim` / `omips`,
submissions in both; confirm with curl and a browser that each namespace
shows only its own contest and updates live, and that deactivating a contest
removes it from CWS but keeps its ranking.

## Known Issues Noticed (out of scope)

- `clear-ranking.sh` still targets `/home/cmsuser/cms/lib/ranking` and a
  `cmsrankingwebserver` supervisor program inside the `cms` container,
  although the ranking now runs in its own container. It is not group-aware
  either. To be revisited separately.
