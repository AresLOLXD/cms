# SQLAlchemy 1.3 → 2.x Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite every `session.query(...)` (legacy SQLAlchemy 1.x `Query` API) call site in the codebase to SQLAlchemy 2.0's `select()`/`session.execute()`/`session.get()` style, behavior-preserving, package by package, with the full test suite green after each package.

**Architecture:** A pure syntax migration applying one fixed mechanical mapping table everywhere — no call site gets a novel pattern. Six passes, one per package, in dependency order (`cms/db/` first since everything else calls into it, `cmstestsuite/databasemixin.py` alongside it since it's exercised from pass 1 onward). Each pass ends with a `grep` proving zero `session.query(`/`.query(` call sites remain in that pass's files, plus a full-suite green run against the real pinned dependencies (via `docker-compose.test.yml`, not an ad hoc local venv).

**Tech Stack:** Python 3.12, SQLAlchemy (`>=1.3,<1.4` today → `>=2.0,<2.1`), PostgreSQL, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-sqlalchemy-2x-migration-design.md`

## Global Constraints

- Behavior-preserving only: if a rewritten query's test result differs from before, that's a bug in the rewrite to fix, never an intentional change.
- `cmsranking/` is out of scope — confirmed it uses no SQLAlchemy (its own in-memory `Store`), do not touch it.
- No schema/model changes, no new indexes, no change to any public function's parameter list. `cms/db/util.py`'s builder functions (`get_submissions`, `get_submission_results`, etc.) keep their exact names and parameters; only their return type and internal query construction change.
- `beta` branch only. Never touch `main`.
- `sqlalchemy>=2.0,<2.1` target range, on Python 3.12 (this line's pinned dev/Docker version). Per a ruling made before any code was written (Task 1 Step 1.5), the pin is bumped as the FIRST code change in Task 1, not deferred to Task 6 as originally drafted — every task's Docker-based verification (Tasks 1-5) therefore runs against `sqlalchemy>=2.0,<2.1`, never against the old `1.3.24`.
- Verify every package's pass against the **real Docker test image** (`docker/docker-compose.test.yml`, which builds the production `Dockerfile` with real `constraints.txt` pins), not a local ad hoc `.venv` — local venvs in this repo have repeatedly been found to silently substitute newer dependency versions than what's actually pinned.
- The legacy `Query` API still works under SQLAlchemy 2.0 in a deprecated compatibility mode, so an incomplete rewrite can pass tests and hide as a warning. Every task's last verification step is `grep -rn "\.query(" <that task's files>` returning zero matches, not just a green test run.
- `pyflakes` clean on every touched file.
- **Ruling (added mid-Task 1, after the version bump surfaced breaking changes beyond `.query()`):** bumping the pin to `sqlalchemy>=2.0,<2.1` breaks several unrelated APIs that have nothing to do with the `.query()`→`select()` syntax this plan's mapping table covers: `MetaData(bind=...)`'s positional/keyword `bind` arg (removed in 2.0), `as_declarative(bind=..., constructor=...)`'s `bind`/`constructor` kwargs (removed in 2.0 — `constructor=None` existed to preserve `Base`'s own hand-written `__init__`), SQLAlchemy 2.0's declarative annotation scanner raising `MappedAnnotationError` on any class-level type annotation that isn't wrapped in `Mapped[...]` (this codebase uses classic `Column(...)` + separate bare annotations throughout, not `Mapped[]`), and 2.0's removal of implicit textual SQL execution (`session.execute("raw sql")` now requires `text("raw sql")`). None of these are `.query()` call sites, so none of them are literally in this plan's mapping table — but all of them are **mechanical, behavior-preserving fixes required to keep the exact same code working under 2.0**, not architectural or schema changes: they don't alter table columns, relationships, or model behavior, only how the ORM's setup code is spelled. They are IN SCOPE for every task from here on, do not violate the "no schema/model changes" constraint above, and must be applied wherever the version bump breaks them, using these fixes:
  - `MetaData(engine)` → `MetaData()` + call `metadata.create_all(engine)` explicitly at the site that used to rely on the bound engine.
  - `as_declarative(bind=engine, metadata=metadata, constructor=None)` → drop `bind`/`constructor`; if `constructor=None` was preserving a hand-written `__init__`, save `Base.__init__` before the decorator runs and restore it after, so the custom `__init__` is still what gets called (SQLAlchemy 2.0 always installs its own unless prevented like this).
  - `MappedAnnotationError` on classic-style models → add `__allow_unmapped__ = True` to `Base` in `cms/db/base.py` (one place, applies to every model that inherits from it) — this is SQLAlchemy's own documented opt-out for codebases keeping the classic `Column()` mapping style with plain type annotations, not a per-model change.
  - Raw string SQL passed to `session.execute(...)` → wrap in `text(...)` (`from sqlalchemy import text`), same string, no behavior change.
  - Any other 2.0 breaking change discovered this way (not just these four) is handled the same way: fix it mechanically and behavior-preservingly, note it in the task's report the same as any other finding, and if it's outside this task's originally listed files (like `cms/db/init.py`, discovered mid-Task-1), it's still in scope — fix it where it actually lives.

### The Mechanical Mapping (apply exactly, never improvise)

Reproduced from the spec; every task references this table instead of repeating it. `select` and `func` come from `from sqlalchemy import select, func`. `session.query(...)`/`self.sql_session.query(...)`/`self.sa_session.query(...)`/etc. are all the same pattern — only the session variable name differs by file.

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

**Builder functions that return a query object** (`get_submissions`, `get_submission_results`, and similar in `cms/db/util.py`): change their body to build and `return` a `select(...)` statement instead of `session.query(...)`, and change their return type annotation from `Query` to `Select` (`from sqlalchemy import Select`, replacing the `from sqlalchemy.orm import Query` import if nothing else in the file needs `Query`). Every caller that used to do `get_submissions(session, ...).filter(Y).all()` becomes `session.execute(get_submissions(session, ...).filter(Y)).scalars().all()` — the function call and its `.filter()`/`.where()` chaining stay exactly the same; only the final execution step at the call site changes.

## Review Focus

1. **`Base.get_from_id()` used with a tuple composite-key ID** (some models have multi-column primary keys per its own docstring) — `session.get(cls, id_)` must accept a tuple the same way `session.query(cls).get(id_)` did; a reviewer must confirm this wasn't silently narrowed to single-int IDs only. Owned by Task 1.
2. **`ObjectDeletedError` handling in `get_from_id()`** — the existing `try/except ObjectDeletedError: return None` around the `.get()` call must still catch the same condition after switching to `session.get()`; don't drop it while rewriting. Owned by Task 1.
3. **A builder function called with zero optional filters** (e.g. `get_submissions(session)` with every keyword `None`) — must still return every row, not an empty result, after the `Query`→`Select` return-type change. Owned by Task 1 (the builder functions themselves), exercised again incidentally by Tasks 2–5's own tests.
4. **A `.count()` rewrite silently changing what's counted** (e.g. counting rows of a joined query vs. counting distinct primary entities) — the highest-risk single mapping in the table, since `select(func.count()).select_from(M).where(X)` must count exactly what `session.query(M).filter(X).count()` counted, not an unintended cartesian product if `X` implies a join. Every `.count()` call site gets its own explicit before/after check in whichever task owns it, not just a blanket "apply the table".
5. **Deprecation warnings from an incompletely-migrated call site slipping through as passing-but-noisy tests** — the spec explicitly warns legacy `Query` still works under 2.0 in compat mode. Every task's final verification step is a `grep` for zero remaining `.query(` in that task's files (not just green tests), per Global Constraints; this line exists so a plan reader sees it called out, not only buried in Global Constraints.

---

### Task 1: `cms/db/` foundation + shared test helpers

**Files:**
- Modify: `cms/db/base.py:216` (`Base.get_from_id()`; also gets `__allow_unmapped__ = True` and the `as_declarative()` kwarg fix per the Global Constraints ruling)
- Modify: `cms/db/init.py` (discovered mid-task: `MetaData(engine)`'s `bind` kwarg removed in 2.0, per the Global Constraints ruling — not in the plan's original file list, but where the fix actually lives)
- Modify: `cms/db/util.py` (`get_contest_list`, `get_submissions`, `get_submission_results`, and any other function in this file using `.query(`; imports at the top; also line 71's raw-string `session.execute("select 0;")` needs `text(...)` per the Global Constraints ruling)
- Modify: `cms/db/fsobject.py:425`
- Modify: `cms/db/submission.py:200,467`
- Modify: `cms/db/__init__.py:138`
- Modify: `cmstestsuite/unit_tests/databasemixin.py` (any `.query(` usage in its own helper methods)
- Test: `cmstestsuite/unit_tests/db/*.py` (existing tests for these files)

**Interfaces:**
- Consumes: nothing from earlier tasks (this is the first pass).
- Produces: `Base.get_from_id(cls, id_, session) -> object | None` — same signature and behavior as before, internally uses `session.get(cls, id_)`. `cms.db.util.get_submissions(...) -> Select` and `cms.db.util.get_submission_results(...) -> Select` (changed from `-> Query`) — same parameters, callers chain `.filter()`/`.where()` on the result exactly as before, then must wrap execution in `session.execute(...)`. Every later task's files that call these two functions or `get_from_id` depend on this contract.

- [ ] **Step 1: Confirm the starting baseline is green**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: no failures other than the known-clean baseline established earlier in this project's history (confirm zero unexpected failures before touching any code — if this baseline isn't clean, stop and report before starting the migration). This run is still against the *current* `sqlalchemy>=1.3,<1.4` pin — it only exists to capture the pre-migration pass/fail set for comparison.

- [ ] **Step 1.5: Bump the SQLAlchemy pin now (moved up from Task 6)**

**Ruling (controller, pre-implementation):** the plan originally deferred
the version bump to Task 6 while asking Task 1-5 to verify their rewrites
against the *currently pinned* `sqlalchemy==1.3.24` via the real Docker
image. That pin has no `Session.get()` (added in 1.4) and no
`sqlalchemy.future`-style `select()` with `.filter()`/`.filter_by()` at
all — every rewritten call site in this migration would raise
`AttributeError` immediately under 1.3.24, not fail behaviorally. This is
a structural plan-ordering defect, not a per-call-site ambiguity, caught
by Task 1's implementer before any code was touched. Ruling: bump the pin
here, before Step 2, so every task's Docker-based verification (Tasks 1-5)
runs against `sqlalchemy>=2.0,<2.1` from the start. Cost if wrong: would
need to re-verify Tasks 1-5's baselines a second time after moving the
bump back — fully recoverable via git history, since each task commits
separately.

In `pyproject.toml`, change:

```python
    "sqlalchemy>=1.3,<1.4",   # http://docs.sqlalchemy.org/en/latest/changelog/index.html
```

to:

```python
    "sqlalchemy>=2.0,<2.1",   # http://docs.sqlalchemy.org/en/latest/changelog/index.html
```

Then regenerate `constraints.txt`'s pin. Build a throwaway Python 3.12 venv
with the new constraint (do not touch the project's own `.venv`), confirm
the resolved version, and update the `SQLAlchemy==1.3.24` line in
`constraints.txt` to match:

```bash
docker run --rm -v "$PWD:/src-ro:ro,Z" -w /work python:3.12-slim bash -c "
  mkdir -p /work && cp -r /src-ro/. /work/ && cd /work
  apt-get update -qq && apt-get install -y -qq build-essential libpq-dev libffi-dev libyaml-dev >/dev/null 2>&1
  python3 -m venv /tmp/v && /tmp/v/bin/pip install -q -U pip
  /tmp/v/bin/pip install -q 'sqlalchemy>=2.0,<2.1'
  /tmp/v/bin/pip show sqlalchemy | grep -E 'Name|Version'
"
```

Edit `constraints.txt`'s `SQLAlchemy==1.3.24` line to the exact version
this prints. Then rebuild the Docker test image once so subsequent steps
in this task (and every later task) use the bumped dependency:
`docker compose -p cms-beta -f docker/docker-compose.test.yml build --no-cache testcms`.
Do NOT commit `pyproject.toml`/`constraints.txt` as a separate commit —
fold them into this task's Step 10 commit alongside the `cms/db/` rewrite,
since the version bump and the first package's rewrite land together and
must be reviewed together (an installed-but-unused 2.0 pin with no
rewritten code, or vice versa, is not a valid intermediate state).

- [ ] **Step 2: Rewrite `Base.get_from_id()`**

In `cms/db/base.py`, replace:

```python
        try:
            # The .get() method returns None if the object isn't in the
            # identity map of the session nor in the database, but
            # raises ObjectDeletedError in case it was in the identity
            # map, got marked as expired but couldn't be found in the
            # database again.
            return session.query(cls).get(id_)
        except ObjectDeletedError:
            return None
```

with:

```python
        try:
            # session.get() returns None if the object isn't in the
            # identity map of the session nor in the database, but
            # raises ObjectDeletedError in case it was in the identity
            # map, got marked as expired but couldn't be found in the
            # database again.
            return session.get(cls, id_)
        except ObjectDeletedError:
            return None
```

- [ ] **Step 3: Run the DB-layer tests for `get_from_id`**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest cmstestsuite/unit_tests/db/ -v"`
Expected: all pass, including any test exercising a composite (tuple) primary key model, per Review Focus item 1.

- [ ] **Step 4: Rewrite `cms/db/util.py`**

Change the import line:

```python
from sqlalchemy.orm import Query
```

to:

```python
from sqlalchemy import select, func, Select
```

(keep any other existing `from sqlalchemy import ...` names already in the file; merge into one import statement per the file's existing style — check the file's current import block before editing.)

Replace:

```python
    return session.query(Contest).all()
```

with:

```python
    return session.execute(select(Contest)).scalars().all()
```

Replace (in `get_submissions`, whose signature and every intermediate `query = query.filter(...)` / `query = query.join(...)` line stays exactly as-is — only the initial construction and the final `return` change):

```python
    query = session.query(Submission)
```

with:

```python
    query = select(Submission)
```

and its final `return query` line stays `return query` (now returning a `Select`).

Do the same construction change for `get_submission_results` — replace:

```python
    query = session.query(SubmissionResult).join(Submission)
```

with:

```python
    query = select(SubmissionResult).join(Submission)
```

— every subsequent `query = query.filter(...)` line in that function is unchanged, and its final `return query` line is unchanged (now returning a `Select`).

Update both functions' return type annotations from `-> Query:` to `-> Select:`.

Apply the same construction-only change (initial `session.query(M)` → `select(M)`, keep every chained `.filter()`/`.join()` line, change the return annotation) to every other `.query(` call site remaining in this file — re-run `grep -n "\.query(" cms/db/util.py` after each change and confirm the line is gone before moving to the next.

- [ ] **Step 5: Rewrite the remaining `cms/db/` call sites**

In `cms/db/fsobject.py:425`, `cms/db/submission.py:200`, `cms/db/submission.py:467`, `cms/db/__init__.py:138`: read each line in context (a few lines before/after) and apply the matching row from the Global Constraints mapping table based on which terminal method it calls (`.all()`, `.one()`, `.first()`, `.count()`, plain iteration, etc.). Add the `select`/`func` import to each file's existing `from sqlalchemy import ...` line if not already present (check each file first — don't add a duplicate import statement).

- [ ] **Step 5.5: Repo-wide sweep for other SQLAlchemy 2.0 breaking changes (added mid-Task 1, per the ruling above)**

Since bumping the pin surfaced breaking changes unrelated to `.query()` (see the Global Constraints ruling above), sweep the *whole* repo now — not just this task's files — for the same risk categories, so Tasks 2-6 aren't blocked by the same class of surprise later. Run each of these and read every hit in context:

```bash
grep -rn '\.execute(\s*["'"'"']' cms/ cmscommon/ cmscontrib/ cmstestsuite/   # raw string SQL missing text()
grep -rn 'MetaData(' cms/ cmscommon/ cmscontrib/ cmstestsuite/               # bind= kwarg removed in 2.0
grep -rn 'as_declarative(' cms/ cmscommon/ cmscontrib/ cmstestsuite/          # bind=/constructor= kwargs removed
grep -rn '\.with_entities(' cms/ cmscommon/ cmscontrib/ cmstestsuite/         # Query-only method, no Select equivalent by that name
```

Fix every genuine hit the same way item 3/4 above were fixed (mechanical, behavior-preserving). Note in the task report which files outside this task's original list you touched and why. This sweep's job is to surface problems now, in the foundation task, not mid-way through Tasks 2-6.

- [ ] **Step 6: Confirm `cmstestsuite/unit_tests/databasemixin.py` needs no changes**

Run: `grep -c "\.query(" cmstestsuite/unit_tests/databasemixin.py`
Expected: `0` (confirmed already — this shared DB-test fixture has no `.query(` calls of its own; the 30 test-file call sites elsewhere in `cmstestsuite/` live in specific test files migrated alongside the production package they test, in Tasks 4 and 5). If this ever prints a nonzero count (e.g. someone added a `.query(` call to this file after this plan was written), treat it the same as any other file: read it in context and apply the Global Constraints mapping table.

- [ ] **Step 7: Verify zero remaining legacy query syntax in this task's files**

Run: `grep -rn "\.query(" cms/db/base.py cms/db/util.py cms/db/fsobject.py cms/db/submission.py cms/db/__init__.py cmstestsuite/unit_tests/databasemixin.py`
Expected: no output (zero matches).

- [ ] **Step 8: Run the full test suite against the real Docker test image**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Step 1's baseline (this task's changes ripple into every other package's tests since they all depend on `cms/db/`, so the full suite — not just `cmstestsuite/unit_tests/db/` — is the real gate).

**Ruling (added mid-Task 1, after Step 8 surfaced an unavoidable cross-task gap):** Task 1 changes `get_submissions()`/`get_submission_results()`'s return type to `Select`, and Task 1's own Interfaces section already says every later task's files that call them "must wrap their own final `.all()`/`.first()`/etc. in `session.execute(...)`" — meaning callers in `cms/service/` (Task 2's files) are *expected* to break the moment Task 1 lands, not because of a Task 1 defect. A genuinely green Step 8 is only achievable once Task 2 also lands. **Accepted exception:** if Step 8's failures are 100% confined to files in Task 2's file list, and each one is fully root-caused as either (a) this exact, anticipated `Select`-return-type ripple, or (b) another version-bump-caused break in a Task 2 file discovered incidentally while diagnosing (fix it note-only here, actually applying the fix is Task 2's job) — commit Task 1 with this documented, diagnosed gap and dispatch Task 2 immediately next to close it. Any failure outside Task 2's file list, or one that isn't fully root-caused, is NOT covered by this exception and must be fixed before Task 1 is committed.

- [ ] **Step 9: pyflakes**

Run: `.venv/bin/pyflakes cms/db/base.py cms/db/util.py cms/db/fsobject.py cms/db/submission.py cms/db/__init__.py cmstestsuite/unit_tests/databasemixin.py`
Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add cms/db/base.py cms/db/util.py cms/db/fsobject.py cms/db/submission.py cms/db/__init__.py cmstestsuite/unit_tests/databasemixin.py pyproject.toml constraints.txt
git commit -m "refactor(db): migrate cms/db/ to SQLAlchemy 2.0 select() style

Bumps the sqlalchemy pin from >=1.3,<1.4 to >=2.0,<2.1 (moved up from
Task 6 — see plan ruling on Task 1 Step 1.5): every later task's Docker
verification depends on the 2.0 API being installed, not just this one."
```

---

### Task 2: `cms/service/`

**Files:**
- Modify: `cms/service/esoperations.py` (9 call sites)
- Modify: `cms/service/EvaluationService.py` (2 call sites)
- Modify: `cms/service/ProxyService.py` (1 call site)
- Modify: `cms/service/scoringoperations.py` (1 call site)
- Test: `cmstestsuite/unit_tests/service/*.py` (existing tests for these files, e.g. `esoperations_test.py`, `proxyservice_groups_test.py`, `ProxyServiceTest.py`)

**Interfaces:**
- Consumes: `Base.get_from_id()` (unchanged signature, Task 1), `cms.db.util.get_submissions`/`get_submission_results` (now return `Select`, Task 1) — this task's files call these and must wrap their own final `.all()`/`.first()`/etc. in `session.execute(...)`.
- Produces: nothing new consumed by later tasks — this package is a leaf relative to Tasks 3–5.

- [ ] **Step 1: Confirm baseline is Task 1's actual green state (with a known, documented gap)**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: **828 passed, 25 failed, 7 skipped** — NOT the original pre-migration baseline (853 passed, 0 failed, 7 skipped). Task 1 changed `get_submissions()`/`get_submission_results()` to return `Select` instead of `Query`, and by the plan's own design (see Task 1's Interfaces section and its Step 8 ruling) every caller of those functions outside `cms/db/` breaks until it's updated to wrap the result in `session.execute(...)` — this task is exactly that update. All 25 failures are pre-diagnosed as confined to this task's two root causes:
1. `esoperations.py`: `case([...], else_=...)` needs SQLAlchemy 2.0's positional-args form, `case(*whens, else_=...)` (not a list) — a version-bump break independent of `.query()`, same category as the ones already fixed in Task 1's Global Constraints ruling.
2. `ProxyService.py`: `get_submissions(...).filter(...).filter(...).all()` — `.all()` doesn't exist on a `Select`; wrap the whole chain in `session.execute(...).scalars().all()` per Step 2 below (this is literally the transformation Step 2 already shows).

If Step 1's actual result differs from 828/25/7 — a different count, or any failure outside `esoperations.py`/`ProxyService.py` — stop and report before proceeding; that would mean something changed since Task 1's diagnosis and needs fresh investigation, not blind continuation.

- [ ] **Step 2: Rewrite `cms/service/ProxyService.py`'s caller of `get_submissions`**

Replace:

```python
        submissions = get_submissions(session, contest_id=contest.id) \
            .filter(not_(Participation.hidden)) \
            .filter(Submission.official).all()
```

with:

```python
        submissions = session.execute(
            get_submissions(session, contest_id=contest.id)
            .filter(not_(Participation.hidden))
            .filter(Submission.official)
        ).scalars().all()
```

Check the top of the file for an existing `from sqlalchemy import ...` line; no new import is needed here since `session.execute` is a method call, not a name.

- [ ] **Step 3: Rewrite `cms/service/EvaluationService.py`'s two call sites**

One is a caller of `get_submissions`/`get_submission_results` (same transformation pattern as Step 2 — wrap the existing chain in `session.execute(...).scalars().all()` or the matching terminal method from the mapping table, matching whichever terminal method the current code calls). The other may be a direct `session.query(...)` — read it in context and apply the Global Constraints mapping table row matching its terminal method. Re-run `grep -n "\.query(" cms/service/EvaluationService.py` after and confirm zero remaining matches, and separately confirm every `get_submissions(`/`get_submission_results(` call in this file is now wrapped in `session.execute(...)`.

- [ ] **Step 4: Rewrite `cms/service/esoperations.py`'s 9 call sites, plus its `case()` break**

Read each of the 9 in context (`grep -n "\.query(" cms/service/esoperations.py` to list them) and apply the matching Global Constraints mapping table row per call site based on its terminal method (`.all()`, `.first()`, `.one()`, `.count()`, join usage, etc.). Add `from sqlalchemy import select, func` to the file's imports if not already present. Re-run the grep after each one and confirm the line disappears before moving to the next.

Separately, this file also has a `case([...], else_=...)` call using SQLAlchemy 1.x's list-of-whens form — 2.0 requires the whens as separate positional args: `case(*whens, else_=...)` (unpack the list into positional args, same whens, same else_). This isn't a `.query()` site so the grep above won't find it; it's the version-bump break diagnosed during Task 1's Step 8 (14+3 of that task's 25 confined, expected failures came from this). Find it with `grep -n "case(" cms/service/esoperations.py` and fix it as part of this step.

- [ ] **Step 5: Rewrite `cms/service/scoringoperations.py`'s 1 call site**

Read it in context and apply the matching mapping table row.

- [ ] **Step 6: Verify zero remaining legacy query syntax in this task's files**

Run: `grep -rn "\.query(" cms/service/esoperations.py cms/service/EvaluationService.py cms/service/ProxyService.py cms/service/scoringoperations.py`
Expected: no output.

- [ ] **Step 7: Run the full test suite**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Task 1's baseline.

- [ ] **Step 8: pyflakes**

Run: `.venv/bin/pyflakes cms/service/esoperations.py cms/service/EvaluationService.py cms/service/ProxyService.py cms/service/scoringoperations.py`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add cms/service/esoperations.py cms/service/EvaluationService.py cms/service/ProxyService.py cms/service/scoringoperations.py
git commit -m "refactor(service): migrate cms/service/ to SQLAlchemy 2.0 select() style"
```

---

### Task 3: `cms/server/admin/` (AWS handlers)

**Files:**
- Modify: `cms/server/admin/handlers/admin.py` (1), `base.py` (7), `contest.py` (1), `contestquestion.py` (1), `contestranking.py` (1), `contestsubmission.py` (2), `contesttask.py` (6), `contestuser.py` (11), `dataset.py` (2), `main.py` (2), `rankinggroup.py` (2), `submission.py` (1), `task.py` (3), `user.py` (8), `usertest.py` (1)
- Test: `cmstestsuite/unit_tests/server/admin/*.py`

**Interfaces:**
- Consumes: `Base.get_from_id()`, `cms.db.util.get_submissions`/`get_submission_results` (Task 1). Independent of Task 2.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Confirm baseline is green**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: same pass/fail set as Task 2's Step 7.

- [ ] **Step 2: List every call site to migrate**

Run: `grep -n "\.query(" cms/server/admin/handlers/*.py`
This prints all 52 call sites with file:line. Work through them file by file, largest first (`contestuser.py` with 11, then `contesttask.py` with 6, then `user.py` with 8, `base.py` with 7, then the rest).

- [ ] **Step 3: Rewrite `cms/server/admin/handlers/base.py`'s 7 call sites**

These are used by every other handler file in this package (it's the shared base module), so migrate it first within this task. Read each call site in context and apply the matching Global Constraints mapping table row. Add `from sqlalchemy import select, func` to its imports if not present. Re-run `grep -n "\.query(" cms/server/admin/handlers/base.py` after and confirm zero remaining.

- [ ] **Step 4: Rewrite `contestuser.py` (11), `user.py` (8), `contesttask.py` (6)**

For each file: read every `.query(` call site in context, apply the matching mapping table row per its terminal method, add the `sqlalchemy` import if missing, re-run `grep -n "\.query(" <file>` and confirm zero remaining before moving to the next file.

- [ ] **Step 5: Rewrite the remaining files**

`admin.py`, `contest.py`, `contestquestion.py`, `contestranking.py`, `contestsubmission.py` (2), `dataset.py` (2), `main.py` (2), `rankinggroup.py` (2), `submission.py`, `task.py` (3), `usertest.py`. Same process per file: read in context, apply the mapping table, add imports if missing, `grep`-verify zero remaining before the next file.

- [ ] **Step 6: Verify zero remaining legacy query syntax across the whole package**

Run: `grep -rn "\.query(" cms/server/admin/handlers/`
Expected: no output.

- [ ] **Step 7: Run the full test suite**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Task 2's baseline.

- [ ] **Step 8: pyflakes**

Run: `.venv/bin/pyflakes cms/server/admin/handlers/`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add cms/server/admin/handlers/
git commit -m "refactor(aws): migrate cms/server/admin/handlers/ to SQLAlchemy 2.0 select() style"
```

---

### Task 4: `cms/server/contest/` (CWS handlers) + `cms/grading/`

**Files:**
- Modify: `cms/server/contest/handlers/api.py` (1), `base.py` (1), `contest.py` (4), `main.py` (6), `tasksubmission.py` (2), `taskusertest.py` (1)
- Modify: the one file in `cms/grading/` with a `.query(` call site — find it with `grep -rln "\.query(" cms/grading/`
- Modify: `cmstestsuite/unit_tests/server/contest/communication_test.py` (1 call site), `cmstestsuite/unit_tests/server/contest/submission/workflow_test.py` (2 call sites)
- Test: `cmstestsuite/unit_tests/server/contest/*.py`, relevant `cmstestsuite/unit_tests/grading/*.py`

**Interfaces:**
- Consumes: `Base.get_from_id()`, `cms.db.util.get_submissions`/`get_submission_results` (Task 1). Independent of Tasks 2 and 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Confirm baseline is green**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: same pass/fail set as Task 3's Step 7.

- [ ] **Step 2: Rewrite `cms/server/contest/handlers/base.py`'s 1 call site**

This is the shared base module for this package — migrate first. Read it in context, apply the matching mapping table row, add the `sqlalchemy` import if missing.

- [ ] **Step 3: Rewrite `main.py` (6 call sites)**

Read each in context (`grep -n "\.query(" cms/server/contest/handlers/main.py`), apply the matching mapping table row, add imports if missing, `grep`-verify zero remaining in the file before moving on.

- [ ] **Step 4: Rewrite the remaining files**

`api.py` (1), `contest.py` (4), `tasksubmission.py` (2), `taskusertest.py` (1). Same process per file.

- [ ] **Step 5: Rewrite the `cms/grading/` call site**

Find it: `grep -rn "\.query(" cms/grading/`. Read it in context and apply the matching mapping table row.

- [ ] **Step 6: Rewrite the 2 CWS test files**

`cmstestsuite/unit_tests/server/contest/communication_test.py` (1 call site) and `cmstestsuite/unit_tests/server/contest/submission/workflow_test.py` (2 call sites). Read each `.query(` call in context and apply the matching mapping table row; these are test setup/assertion code, so the terminal method is usually `.all()`, `.first()`, `.one()`, or `.count()` — read the actual line, don't assume.

- [ ] **Step 7: Verify zero remaining legacy query syntax**

Run: `grep -rn "\.query(" cms/server/contest/handlers/ cms/grading/ cmstestsuite/unit_tests/server/contest/`
Expected: no output.

- [ ] **Step 8: Run the full test suite**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Task 3's baseline.

- [ ] **Step 9: pyflakes**

Run: `.venv/bin/pyflakes cms/server/contest/handlers/ cms/grading/ cmstestsuite/unit_tests/server/contest/`
Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add cms/server/contest/handlers/ cms/grading/ cmstestsuite/unit_tests/server/contest/
git commit -m "refactor(cws): migrate cms/server/contest/handlers/ and cms/grading/ to SQLAlchemy 2.0 select() style"
```

---

### Task 5: `cmscontrib/`

**Files:**
- Modify: `AddParticipation.py` (5), `AddStatement.py` (2), `AddSubmission.py` (2), `AddTestcases.py` (3), `CleanFiles.py` (1), `DumpExporter.py` (3), `ExportSubmissions.py` (1), `ImportContest.py` (6), `ImportDataset.py` (1), `importing.py` (1), `ImportTask.py` (1), `ImportTeam.py` (1), `ImportUser.py` (1), `PrometheusExporter.py` (13), `RemoveContest.py` (1), `RemoveParticipation.py` (3), `RemoveSubmissions.py` (6), `RemoveTask.py` (2), `RemoveUser.py` (1), `SetupDB.py` (2), `TelegramBot.py` (2) — all under `cmscontrib/`
- Modify (test files, 27 call sites across 12 files): `cmstestsuite/unit_tests/cmscontrib/DumpImporterTest.py` (5), `SetupDBTest.py` (4), `ImportContestTest.py` (3), `AddSubmissionTest.py` (3), `ImportTaskTest.py` (2), `ImportUserTest.py` (2), `AddStatementTest.py` (2), `DumpExporterTest.py` (2), `AddAdminTest.py` (1), `ImportTeamTest.py` (1), `ImportDatasetTest.py` (1), `AddParticipationTest.py` (1)
- Test: `cmstestsuite/unit_tests/cmscontrib/*.py`

**Interfaces:**
- Consumes: `Base.get_from_id()`, `cms.db.util.get_submissions`/`get_submission_results` (Task 1). Independent of Tasks 2–4.
- Produces: nothing consumed by later tasks (final production-code task).

- [ ] **Step 1: Confirm baseline is green**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: same pass/fail set as Task 4's Step 7.

- [ ] **Step 2: Rewrite `PrometheusExporter.py` (13 call sites)**

Largest file in this package. Read each in context (`grep -n "\.query(" cmscontrib/PrometheusExporter.py`), apply the matching mapping table row per its terminal method, add `from sqlalchemy import select, func` if missing, `grep`-verify zero remaining in the file.

- [ ] **Step 3: Rewrite `ImportContest.py` (6) and `RemoveSubmissions.py` (6)**

Same process per file.

- [ ] **Step 4: Rewrite `AddParticipation.py` (5)**

Same process.

- [ ] **Step 5: Rewrite `AddTestcases.py` (3), `DumpExporter.py` (3), `RemoveParticipation.py` (3)**

Same process per file. For `DumpExporter.py`, note this file already has fork-specific logic (an unconditional skip of the `Contest.ranking_group` relationship, from an earlier feature) — read the surrounding code carefully so the query-syntax rewrite doesn't touch that unrelated logic, only the `.query(` call sites themselves.

- [ ] **Step 6: Rewrite the remaining production files**

`AddStatement.py` (2), `AddSubmission.py` (2), `RemoveTask.py` (2), `SetupDB.py` (2), `TelegramBot.py` (2), `CleanFiles.py` (1), `ExportSubmissions.py` (1), `ImportDataset.py` (1), `importing.py` (1), `ImportTask.py` (1), `ImportTeam.py` (1), `ImportUser.py` (1), `RemoveContest.py` (1), `RemoveUser.py` (1). Same process per file.

- [ ] **Step 7: Rewrite the 12 `cmscontrib` test files**

`DumpImporterTest.py` (5), `SetupDBTest.py` (4), `ImportContestTest.py` (3), `AddSubmissionTest.py` (3), `ImportTaskTest.py` (2), `ImportUserTest.py` (2), `AddStatementTest.py` (2), `DumpExporterTest.py` (2), `AddAdminTest.py` (1), `ImportTeamTest.py` (1), `ImportDatasetTest.py` (1), `AddParticipationTest.py` (1), all under `cmstestsuite/unit_tests/cmscontrib/`. Same process per file: read each `.query(` call in context, apply the matching mapping table row, `grep`-verify zero remaining in the file before moving to the next.

- [ ] **Step 8: Verify zero remaining legacy query syntax across the whole package**

Run: `grep -rn "\.query(" cmscontrib/ cmstestsuite/unit_tests/cmscontrib/`
Expected: no output.

- [ ] **Step 9: Run the full test suite**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Task 4's baseline.

- [ ] **Step 10: pyflakes**

Run: `.venv/bin/pyflakes cmscontrib/ cmstestsuite/unit_tests/cmscontrib/`
Expected: no output.

- [ ] **Step 11: Commit**

```bash
git add cmscontrib/ cmstestsuite/unit_tests/cmscontrib/
git commit -m "refactor(cmscontrib): migrate to SQLAlchemy 2.0 select() style"
```

---

### Task 6: Final full-branch verification

**Files:** none modified (verification-only task — the version bump that
was originally planned here moved to Task 1 Step 1.5; see the ruling
there. `pyproject.toml`/`constraints.txt` were already committed as part
of Task 1).

**Interfaces:**
- Consumes: every file touched by Tasks 1–5, and the SQLAlchemy 2.0.x pin bumped in Task 1.
- Produces: nothing (terminal task).

- [ ] **Step 1: Full clean install and test run against the bumped pin, via the real Docker test image**

Run: `docker compose -p cms-beta -f docker/docker-compose.test.yml build --no-cache testcms && docker compose -p cms-beta -f docker/docker-compose.test.yml run --rm testcms bash -c "dropdb --if-exists --host=testdb --username=postgres cmsdbfortesting && createdb --host=testdb --username=postgres cmsdbfortesting && cmsInitDB && pytest -q"`
Expected: identical pass/fail set to Task 5's baseline — this `--no-cache` rebuild forces a genuinely fresh install against `constraints.txt`/`pyproject.toml` as they stand after Tasks 1-5, not a cached layer.

- [ ] **Step 2: Whole-repo verification that zero legacy query syntax remains**

Run: `grep -rn "\.query(" cms/ cmscommon/ cmscontrib/ cmstestsuite/`
Expected: no output outside `cmsranking/` (which this grep doesn't even include, since it's out of scope) and outside this plan/spec's own markdown files (not matched by this grep pattern against `.py` directories anyway).

- [ ] **Step 3: pyflakes across the whole touched surface**

Run: `.venv/bin/pyflakes cms cmscommon cmscontrib cmstestsuite`
Expected: no new warnings beyond whatever pre-existing ones were already known before this migration started (compare against the pre-migration pyflakes output if in doubt).

- [ ] **Step 4: No commit needed**

Nothing to commit — this task modifies no files (the pin bump landed in
Task 1's commit; every subsequent commit already happened in Tasks 1-5).
This step exists to document that Steps 1-3 above are this task's full
verification, with no follow-up commit expected.
