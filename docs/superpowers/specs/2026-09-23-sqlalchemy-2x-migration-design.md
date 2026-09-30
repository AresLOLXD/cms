# SQLAlchemy 1.3 → 2.x Migration — Design Spec

**Date:** 2026-09-23
**Status:** Approved
**Branch:** `beta` only (see "Relationship to `main`")

## Problem

CMS pins `sqlalchemy>=1.3,<1.4` and uses its legacy `Query` API
(`session.query(Model).filter(...)`) throughout: 158 call sites in
production code (`cms/`, `cmscontrib/`) plus 30 in test fixtures
(`cmstestsuite/`). This is sub-project 2.1 of the `beta` line's broader
modernization effort (see the parent conversation's decomposition): a later
sub-project (2.3) will move CMS's database access to SQLAlchemy's
`AsyncSession`, which **only** supports the modern `select()`/`execute()`
API — the legacy `Query` API does not exist in async mode at all.

Doing a minimal version-bump now (patching only what breaks under 1.3→2.0
compatibility mode) would mean revisiting every one of these 188 call sites
a second time when 2.3 arrives, this time under the added pressure of also
switching the driver to async. This spec does the full rewrite once.

## Goals

- Every `session.query(...)` call site in the codebase rewritten to
  SQLAlchemy 2.0's `select()`/`session.execute()`/`session.get()` style.
- Behavior-preserving: the full test suite passes identically before and
  after (same pass/fail set), for every package along the way.
- `sqlalchemy>=2.0,<2.1` pinned in `pyproject.toml` and `constraints.txt`,
  installed and verified on Python 3.12 (this line's target dev/Docker
  Python, per the earlier Python-pinning work).
- Leaves the database schema, the `Base`/model class hierarchy, and every
  public function signature in `cms/db/util.py` unchanged, so callers
  outside this migration (later sub-projects, and any code not touched yet)
  are not forced to change at the same time.

## Non-Goals

- Async database access (`AsyncSession`, an async driver replacing
  `psycopg2`) — sub-project 2.3, depends on this one.
- Any change to `cmsranking/` — confirmed it does not use SQLAlchemy at all
  (RWS has its own in-memory `Store` persistence, entirely separate from
  `cms/db`). Out of scope for the whole `beta` modernization effort, not
  just this sub-project.
- Any change to what a query actually returns or how it's filtered —
  this is a syntax migration, not a behavior change. If a rewritten query's
  test result differs from before, that is a bug in the rewrite, not an
  intentional improvement, and must be fixed to match the original
  behavior exactly.
- Schema/model changes, migrations, or new indexes.

## Relationship to `main`

This work happens on `beta` only. `main` stays aligned with upstream
(which still pins SQLAlchemy 1.3.x) and continues to receive `beta`'s
non-modernization fixes (like the `.python-version` pin, the CVE patches,
and the `SetupDBTest` fix already cherry-picked/merged both ways) through
the existing pattern: implement on `main` first when a change is equally
useful to both lines, or merge `main`→`beta` when `beta` needs upstream's
latest — never the other way around for this specific migration, since
`main` must keep working against SQLAlchemy 1.3-style code as long as it
tracks upstream.

## Package Order and Scope

One pass per package, in this order, because each depends on the one(s)
before it:

| # | Package | `.query(` call sites | Notes |
|---|---|---|---|
| 1 | `cms/db/` | 10 | Foundation. Includes `Base.get_from_id()` (`cms/db/base.py:216`), the single most-used query pattern in the whole codebase. |
| 2 | `cms/service/` | 13 | Backend services: Worker, EvaluationService, ProxyService, ScoringService, ResourceService, Checker, LogService. |
| 3 | `cms/server/` | 76 | AWS + CWS handlers. Largest single package. |
| 4 | `cms/grading/` | 1 | Trivial, folded into the same pass as `cms/server/` or done on its own — implementer's call given it's one call site. |
| 5 | `cmscontrib/` | 58 | Import/export/admin CLI scripts. |
| 6 | `cmstestsuite/` | 30 | Test files with their own `.query(` calls: 27 across 12 files under `cmstestsuite/unit_tests/cmscontrib/`, 3 across 2 files under `cmstestsuite/unit_tests/server/contest/`. (`cmstestsuite/unit_tests/databasemixin.py`, the shared DB-test fixture, has zero `.query(` calls of its own — confirmed directly — so it needs no migration.) Each test file is updated in the same pass as the production package it tests: the `server/contest/` test files in pass 3 (`cms/server/`), the `cmscontrib/` test files in pass 4 (`cmscontrib/`).

Total: 158 production call sites + 30 test call sites = 188 (the 189th
match from the original grep was `cmsranking/Scoring.py`, confirmed
unrelated to SQLAlchemy).

## Mechanical Mapping

Every rewrite in every package follows this fixed table. No call site gets
a novel pattern invented for it; if a call site doesn't fit the table,
that's a signal to stop and look at it closely rather than improvise.

| 1.x (`Query` API) | 2.0 (`select()`/`session.execute()`) |
|---|---|
| `session.query(M).filter(X).all()` | `session.execute(select(M).where(X)).scalars().all()` |
| `session.query(M).get(id)` | `session.get(M, id)` |
| `session.query(M).filter(X).one()` | `session.execute(select(M).where(X)).scalar_one()` |
| `session.query(M).filter(X).first()` | `session.execute(select(M).where(X)).scalars().first()` |
| `session.query(M).filter(X).count()` | `session.execute(select(func.count()).select_from(M).where(X)).scalar_one()` |
| `session.query(M).join(Other)` | `select(M).join(Other)` (unchanged syntax) |
| `session.query(M).filter_by(x=y)` | `select(M).filter_by(x=y)` (2.0's `Select` keeps `.filter_by()`) |
| `session.query(M).order_by(...)` | `select(M).order_by(...)` (unchanged syntax) |

`func` and `select` are imported from `sqlalchemy` (`from sqlalchemy import
select, func`), matching how the codebase already imports other
SQLAlchemy names.

### Shared query-builder functions (`cms/db/util.py`)

Functions like `get_submissions`, `get_submission_results`,
`get_datasets_to_judge` currently build and *return* a `Query` object,
which callers chain further (`.filter(...)`) and then execute
(`.all()`/`.first()`/etc.) themselves. In 2.0 these functions instead build
and return a `Select` object — `Select` also supports `.filter()`/`.where()`
as chaining methods, so **the function signatures and their callers'
chaining code (`get_submissions(session, contest_id=X).filter(Y)`) do not
need to change**. What changes at every call site is only the final
execution step: a caller that used to do
`get_submissions(session, ...).filter(Y).all()` now does
`session.execute(get_submissions(session, ...).filter(Y)).scalars().all()`.
This is why the migration's effort is spread across `cms/service/` and
`cms/server/` rather than contained to `cms/db/util.py` alone — pass 1
changes what these functions *return*, but every caller elsewhere still
needs its own execution-step rewrite in its own pass.

## Verification Strategy

After each package's pass:

1. Run the **full** test suite (`.venv/bin/pytest -q`), not just the tests
   for that package — `cms/db/` and `cmstestsuite/databasemixin.py` are
   exercised by every other package's tests, so a regression there can only
   be caught by the whole suite, not a package-scoped run.
2. Compare against the known-clean baseline established in this
   conversation: 0 unrelated failures with the real pinned dependency
   versions (confirmed via `docker/docker-compose.test.yml`, which builds
   the actual production `Dockerfile` and its real `constraints.txt`
   pins — this is the authoritative environment to verify against, not an
   ad hoc local venv, which has repeatedly been shown in this conversation
   to silently substitute newer dependency versions than what's actually
   pinned).
3. `pyflakes` clean on every touched file.
4. Only once a package's pass is fully green does the next package's pass
   begin.

## Risks and Mitigations

- **Silent behavior drift** (e.g. a `.filter()` chain subtly reordered, a
  `.count()` rewritten to a different, wrong SQL query): mitigated by the
  mechanical mapping table (no improvisation) and full-suite verification
  after every package, not just spot-checking the file that changed.
- **`Base.get_from_id()` is the single highest-leverage call site** in the
  codebase — get it right first, in isolation, with its own dedicated test
  coverage check, before touching anything downstream that depends on it.
- **Dev environment version substitution** (documented extensively earlier
  in this conversation: `babel`, `tornado`, `psycopg2` have all silently
  resolved to different versions than pinned in various ad hoc venvs used
  during this work): every verification step in this migration must use
  either the real Docker test image (`docker-compose.test.yml`) or a venv
  built with the exact pins from `constraints.txt` on Python 3.12 — never
  trust a pre-existing local `.venv` without first confirming its installed
  SQLAlchemy version matches the pin.
- **Legacy `Query` API still technically works under SQLAlchemy 2.0** (in
  a deprecated compatibility mode) — this means an incomplete rewrite could
  go unnoticed at import time and only surface as a deprecation warning.
  The verification step must explicitly grep the fully-migrated codebase
  for `\.query\(` after each package's pass and confirm zero remaining
  matches in that package's files (excluding files not yet migrated in a
  later pass), not just rely on the test suite being green.

## Testing

- Unit tests: the existing suite is the primary safety net (858+ tests as
  of this conversation). No new tests are added by this migration — it is
  a syntax-preserving rewrite, and the existing suite's assertions already
  pin the *behavior* each rewritten query must continue to produce.
- Manual spot-check: after `cms/db/` (pass 1), directly exercise
  `Contest.get_from_id()` (or equivalent) in a REPL/one-off script against
  the real test DB, independent of the automated suite, given how
  central this one function is.
- Final full-branch check: once all 6 passes are done, run the full suite
  once more via `docker-compose.test.yml` (the real production image),
  and `grep -rn "\.query(" cms/ cmscommon/ cmscontrib/ cmstestsuite/`
  should return zero matches anywhere outside `cmsranking/` (out of
  scope) and outside this spec's own file/comments.
