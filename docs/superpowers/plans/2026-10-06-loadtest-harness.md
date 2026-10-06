# Load-Test Harness and Upstream Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the recovered contest-shaped load-test harness into the repo
as `cmstestsuite/loadtest/`, make it run the same scenario against the fork
and against stock upstream `cc9dfafb`, then run the baseline and the ramp
and write the comparison report (#7).

**Architecture:** Each target image is built from a git ref with that
ref's own Dockerfile. Both targets run with the same hand-rendered
`cms.toml`, and the same `start.sh` starts every service explicitly. A
`portable` profile (no `depends_on`, no two-phase, one contest ranked at
the RWS root) runs on both builds, and a `full` profile runs on the fork
only. The driver, monitor and analyzer are the recovered ones with small
profile-aware changes. A new `compare.py` tabulates runs side by side.

**Tech Stack:** Python 3.12 (stdlib; aiohttp in the driver container),
bash, Docker Compose on the rootful engine (`sg docker -c "docker --context
default ..."`), PostgreSQL 17, pytest for the unit tests.

**Spec:** `docs/superpowers/specs/2026-10-06-loadtest-harness-design.md`

## Global Constraints

- Work only in the worktree `/var/home/areslolxd/Documentos/cms/.worktrees/loadtest`
  (branch `feat/loadtest-harness`). Commit there; never push and never
  touch `main`.
- Recovered source, read-only:
  `/var/home/areslolxd/Documentos/cms/.superpowers/loadtest-recovered-2026-09-30/`.
- Baseline upstream ref: `cc9dfafb`. Fork ref: `beta` (or the branch tip
  when testing harness changes).
- Code and comments in English, PEP 8, pyflakes-clean
  (`uvx -q pyflakes cmstestsuite/loadtest cmstestsuite/unit_tests/loadtest`),
  PEP 484 hints on new functions, and the project docstring format
  (imperative first line, then arg/return/raise lines; see `CONTRIBUTING.md`).
- New files get the license header used by
  `cmstestsuite/unit_tests/asyncwait.py`, with the line
  `# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>`.
- Commits follow Conventional Commits, end with a `Refs #7` line, then the
  trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the
  implementing model's own name).
- Docker always goes through the rootful engine:
  `sg docker -c "docker --context default ..."`. Never run two
  `cmsload-*`/`cmsci-*`/stress stacks at once (isolate cgroups are shared
  with the host).
- Unit tests run with the worktree venv:
  `.venv/bin/python -m pytest -p no:cacheprovider <files>`. If `.venv` is
  missing, create it with
  `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`.
  Tests that need no DB need no `CMS_CONFIG`.
- Profiles: `portable` and `full`. Targets: `fork` and `upstream`. Compose
  project: `cmsload-<target>`. Image tags: `cmsload-<target>:latest`.
- Ports inside the stack: CWS 8888..(8888+N-1), AWS 8898, RWS 8890,
  ProxyService RPC 28600, ES 25000, Workers 26000+i, LogService 29000,
  ScoringService 28500, AdminWebServer RPC 21100, CWS RPC 21000+i.
- `cookie_duration = 18000` on CWS in every run.

## Review Focus

- **Upstream image and the fork's `entrypoint.sh`.** Compose must override
  the entrypoint, otherwise the fork regenerates a config from env vars.
  Pinned by the smoke runs in Task 8 (both targets must log in and score).
- **The `full` profile requested on the `upstream` target** must be refused
  by `run.sh` with a clear message, not run and then report "mismatches".
  Pinned in Task 6, step 4.
- **A run where the driver fails midway** must still collect logs and the
  DB export. Pinned by the `trap` in `run.sh` (Task 6) and checked in
  Task 8 by aborting one smoke run with Ctrl-C.
- **Analyzer on a `portable` run:** an unranked contest must not count as
  RWS mismatches. Pinned by
  `test_rws_check_only_covers_ranked_contests` (Task 5).
- **`compare.py` with runs that lack a metric** (e.g. a run that crashed
  before `docker_stats.log`) prints `-`, not a traceback. Pinned by
  `test_compare_tolerates_missing_metrics` (Task 7).

---

### Task 1: Import the harness as recovered, plus the two cross-build bug fixes

**Files:**
- Create: `cmstestsuite/loadtest/` with every file of the recovered dir:
  `analyze.py compose.yml db_export.py driver.py driver/Dockerfile flushstats.py lockwait.py monitor.py run.sh scenario.py setup_contest.py teardown.sh solutions/* REPORT.md`
  (rename `REPORT.md` to `REPORT-2026-09-30.md`)
- Create: `cmstestsuite/loadtest/__init__.py` (license header plus a
  one-line docstring)
- Create: `cmstestsuite/loadtest/.gitignore`
- Modify: `cmstestsuite/loadtest/db_export.py:27`,
  `cmstestsuite/loadtest/flushstats.py`, `cmstestsuite/loadtest/lockwait.py`

- [ ] **Step 1: Copy the files verbatim and commit them unchanged**

```bash
cd /var/home/areslolxd/Documentos/cms/.worktrees/loadtest
SRC=/var/home/areslolxd/Documentos/cms/.superpowers/loadtest-recovered-2026-09-30
mkdir -p cmstestsuite/loadtest
cp -a "$SRC"/. cmstestsuite/loadtest/
rm -f cmstestsuite/loadtest/.secret_key
git mv -f cmstestsuite/loadtest/REPORT.md cmstestsuite/loadtest/REPORT-2026-09-30.md 2>/dev/null || mv cmstestsuite/loadtest/REPORT.md cmstestsuite/loadtest/REPORT-2026-09-30.md
cat > cmstestsuite/loadtest/.gitignore <<'EOF'
# Run output, rendered config and secrets written by run.sh.
out/
run/
.env.load
.secret_key
EOF
git add cmstestsuite/loadtest
git commit -m "test(loadtest): import the 2026-09-30 load-test harness as recovered

The harness was lost with its temporary directory and was rebuilt from
that session's transcript. It is committed unchanged here so later
commits show every adaptation.

Refs #7"
```

(The `__init__.py` is not created yet; the next step adds it.)

- [ ] **Step 2: Add the package marker**

`cmstestsuite/loadtest/__init__.py`:

```python
#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# (same AGPL block as cmstestsuite/unit_tests/asyncwait.py)

"""Contest-shaped load test of a CMS deployment (see README.md)."""
```

Copy the full AGPL paragraph from `cmstestsuite/unit_tests/asyncwait.py`
lines 1-17; do not abbreviate it.

- [ ] **Step 3: Fix `db_export.py` for both builds**

`task_score` lost its `rounded` parameter in e0e4f8b7 (it always rounds
now). Change line 27 from

```python
                    score, partial = task_score(p, task, rounded=True)
```

to

```python
                    score, partial = task_score(p, task)
```

- [ ] **Step 4: Make the log patterns build-neutral**

In `flushstats.py`, the flush end is logged from `_write_results_sync` on
the fork and from `write_results` upstream; and the year is hard-coded in
the log glob. Replace

```python
for f in sorted(glob.glob(os.path.join(run, 'cmslog', 'EvaluationService-0', '2026*.log'))):
```

with

```python
for f in sorted(glob.glob(os.path.join(run, 'cmslog', 'EvaluationService-0', '[0-9]*.log'))):
```

and

```python
        elif '_write_results_sync] Done' in l and cur:
```

with

```python
        elif re.search(r'write_results(_sync)?\] Done', l) and cur:
```

adding `re` to the `import` line. In `lockwait.py`, replace both
`'2026*.log'` globs with `'[0-9]*.log'`.

- [ ] **Step 5: Lint**

Run: `uvx -q pyflakes cmstestsuite/loadtest`
Expected: no output. If the recovered code has pyflakes warnings (unused
imports or variables), fix only those, minimally.

- [ ] **Step 6: Commit**

```bash
git add cmstestsuite/loadtest
git commit -m "fix(loadtest): run db_export and the log scripts on both builds

task_score no longer takes rounded=; ES logs the flush end from
write_results upstream and _write_results_sync on the fork; log globs no
longer hard-code the year.

Refs #7"
```

---

### Task 2: Scenario profiles

**Files:**
- Modify: `cmstestsuite/loadtest/scenario.py`
- Create: `cmstestsuite/unit_tests/loadtest/__init__.py` (empty, with license header)
- Test: `cmstestsuite/unit_tests/loadtest/scenario_test.py`

**Interfaces:**
- Produces:
  - `scenario.PROFILES: tuple[str, ...] = ("portable", "full")`
  - `scenario.RANKED: dict[str, dict[str, str]]`: per profile,
    contest name → RWS scores path relative to the RWS base URL
    (`{"portable": {"loada": "scores"}, "full": {"loada": "loada/scores", "loadb": "loadb/scores"}}`)
  - `scenario.task_spec(task_name: str, profile: str) -> dict`
  - `scenario.expected_score(task_name: str, kind: str, profile: str) -> float`
  - `scenario.two_phase(profile: str) -> bool` (`full` → True)

- [ ] **Step 1: Write the failing tests**

`cmstestsuite/unit_tests/loadtest/scenario_test.py` (license header first):

```python
"""Tests for the load-test scenario profiles."""

import unittest

from cmstestsuite.loadtest import scenario


class ExpectedScoreTest(unittest.TestCase):

    def test_full_applies_dependencies(self):
        # cadena: S(10), M(20) needs S, L(30) needs M, L(40) needs L#2.
        # wa_small passes M and L but not S, so the whole chain fails.
        self.assertEqual(
            scenario.expected_score("cadena", "wa_small", "full"), 0.0)

    def test_portable_ignores_dependencies(self):
        self.assertEqual(
            scenario.expected_score("cadena", "wa_small", "portable"),
            90.0)

    def test_full_solution_scores_the_maximum_in_both_profiles(self):
        for profile in scenario.PROFILES:
            self.assertEqual(
                scenario.expected_score("cadena", "ac", profile), 100.0)

    def test_portable_task_specs_have_no_dependencies(self):
        for tasks in scenario.TASKS.values():
            for spec in tasks:
                portable = scenario.task_spec(spec["name"], "portable")
                for sub in portable["subtasks"]:
                    self.assertNotIn("depends_on", sub)

    def test_ranked_contests_per_profile(self):
        self.assertEqual(scenario.RANKED["portable"], {"loada": "scores"})
        self.assertEqual(set(scenario.RANKED["full"]), {"loada", "loadb"})

    def test_two_phase_only_in_full(self):
        self.assertTrue(scenario.two_phase("full"))
        self.assertFalse(scenario.two_phase("portable"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            scenario.task_spec("cadena", "fast")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/loadtest/scenario_test.py -q`
Expected: FAIL (`expected_score()` takes 2 positional arguments / no
`PROFILES`).

- [ ] **Step 3: Implement**

In `scenario.py`, after `SOLUTIONS`, replace `task_spec` and
`expected_score` with:

```python
PROFILES = ("portable", "full")

# Contests published to RWS per profile, with the scores path relative to
# the RWS base URL. Upstream ProxyService ranks one contest at the root.
RANKED = {
    "portable": {"loada": "scores"},
    "full": {"loada": "loada/scores", "loadb": "loadb/scores"},
}


def _check_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError("unknown profile %r (expected one of %s)"
                         % (profile, ", ".join(PROFILES)))


def two_phase(profile: str) -> bool:
    """Return whether two-phase evaluation is on in the profile."""
    _check_profile(profile)
    return profile == "full"


def task_spec(task_name: str, profile: str) -> dict:
    """Return the task's spec as the profile uses it.

    The portable profile drops every depends_on, since upstream CMS has
    no subtask dependencies.

    """
    _check_profile(profile)
    for tasks in TASKS.values():
        for spec in tasks:
            if spec["name"] == task_name:
                if profile == "full":
                    return spec
                return dict(spec, subtasks=[
                    {k: v for k, v in sub.items() if k != "depends_on"}
                    for sub in spec["subtasks"]])
    raise KeyError(task_name)


def expected_score(task_name: str, kind: str, profile: str) -> float:
    """Return the score the solution kind must get on the task."""
    spec = task_spec(task_name, profile)
    passes = SOLUTIONS[kind][3]
    ok: list[bool] = []
    for sub in spec["subtasks"]:
        own = sub["category"] in passes
        deps = all(ok[d] for d in sub.get("depends_on", []))
        ok.append(own and deps)
    return float(sum(sub["max_score"]
                     for sub, good in zip(spec["subtasks"], ok) if good))
```

- [ ] **Step 4: Update the callers**

`grep -n "expected_score\|task_spec" cmstestsuite/loadtest/*.py` and pass
the profile: `analyze.py` reads it from `users["profile"]` (written by
Task 3); `driver.py` does the same if it calls them. Do not change
anything else in those files in this task.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/loadtest/scenario_test.py -q`
Expected: 7 passed. Then `uvx -q pyflakes cmstestsuite/loadtest cmstestsuite/unit_tests/loadtest`: no output.

- [ ] **Step 6: Commit**

`test(loadtest): add the portable and full scenario profiles` (+ `Refs #7`).

---

### Task 3: setup_contest.py works on both builds

**Files:**
- Modify: `cmstestsuite/loadtest/setup_contest.py`
- Test: `cmstestsuite/unit_tests/loadtest/setup_contest_test.py`

**Interfaces:**
- Consumes: `scenario.task_spec(name, profile)`, `scenario.RANKED`.
- Produces:
  - `score_parameters(spec: dict) -> list[dict]` (pure).
  - `contest_extra_kwargs(contest_name: str, profile: str, ranking_groups: dict) -> dict`
    (pure given its inputs; returns `{}` on builds without `Contest.active`).
  - CLI flag `--profile {portable,full}` (required).
  - `users.json` gains `"profile"`, `"ranked": {contest: rws_path}` and
    `"contest_ids": {contest: id}`.

- [ ] **Step 1: Write the failing tests**

`setup_contest_test.py` imports the module by path because
`setup_contest.py` does `sys.path.insert(0, "/loadtest"); import scenario`.
Put `cmstestsuite/loadtest` on `sys.path` first:

```python
"""Tests for the pure helpers of the load-test contest setup."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import scenario  # noqa: E402
import setup_contest  # noqa: E402


class ScoreParametersTest(unittest.TestCase):

    def test_full_keeps_dependencies(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("cadena", "full"))
        self.assertEqual(params[1]["depends_on"], [0])
        self.assertEqual(params[0]["testcases"], "^s1-")

    def test_portable_has_no_dependencies(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("cadena", "portable"))
        self.assertTrue(all("depends_on" not in p for p in params))

    def test_threshold_only_for_group_threshold(self):
        params = setup_contest.score_parameters(
            scenario.task_spec("umbral", "portable"))
        self.assertTrue(all(p["threshold"] == 1.0 for p in params))
        params = setup_contest.score_parameters(
            scenario.task_spec("suma", "portable"))
        self.assertTrue(all("threshold" not in p for p in params))


class ContestExtraKwargsTest(unittest.TestCase):

    def test_fork_full_gets_active_and_group(self):
        groups = {"loada": object(), "loadb": object()}
        kwargs = setup_contest.contest_extra_kwargs(
            "loadb", "full", groups, has_active=True)
        self.assertEqual(kwargs, {"active": True,
                                  "ranking_group": groups["loadb"]})

    def test_fork_portable_is_active_without_group(self):
        kwargs = setup_contest.contest_extra_kwargs(
            "loada", "portable", {}, has_active=True)
        self.assertEqual(kwargs, {"active": True})

    def test_upstream_gets_nothing(self):
        self.assertEqual(setup_contest.contest_extra_kwargs(
            "loada", "portable", {}, has_active=False), {})
```

These import `cms.db` through `setup_contest`, which needs a parseable
config but no DB connection at import time. If the import fails without
`CMS_CONFIG`, export
`CMS_CONFIG=/tmp/claude-1000/-var-home-areslolxd-Documentos-cms/14caaeb2-fee4-4df8-a9f8-351dae7a92f0/scratchpad/cms-test.toml`
when running them, and say so in your report.

- [ ] **Step 2: Run them to verify they fail**

Expected: AttributeError, `score_parameters` does not exist.

- [ ] **Step 3: Implement**

At the top of `setup_contest.py`, replace the `cms.db` import with a
guarded one:

```python
from cms.db import (Contest, Dataset, Group, Participation, SessionGen,
                    Statement, Task, Testcase, User)
try:
    from cms.db import RankingGroup
except ImportError:  # upstream CMS has no ranking groups
    RankingGroup = None
```

Add the two helpers above `main()`:

```python
def score_parameters(spec: dict) -> list[dict]:
    """Return the score type parameters of a task spec.

    spec: a task spec as returned by scenario.task_spec().

    return: one dict per subtask, with depends_on only if the spec has it.

    """
    parameters = []
    for index, sub in enumerate(spec["subtasks"]):
        entry = {"max_score": sub["max_score"],
                 "testcases": "^s%d-" % (index + 1)}
        if spec["score_type"] == "GroupThreshold":
            entry["threshold"] = 1.0
        if sub.get("depends_on"):
            entry["depends_on"] = sub["depends_on"]
        parameters.append(entry)
    return parameters


def contest_extra_kwargs(contest_name: str, profile: str,
                         ranking_groups: dict, has_active: bool) -> dict:
    """Return the Contest() arguments that only the fork knows.

    The fork serves only active contests (CWS answers 404 otherwise) and
    publishes a contest through its ranking group in the full profile.

    contest_name: the contest being created.
    profile: the scenario profile.
    ranking_groups: contest name -> RankingGroup, for the full profile.
    has_active: whether this build's Contest has the "active" column.

    return: the extra keyword arguments for Contest().

    """
    if not has_active:
        return {}
    kwargs: dict = {"active": True}
    if profile == "full":
        kwargs["ranking_group"] = ranking_groups[contest_name]
    return kwargs
```

In `main()`:
- add `parser.add_argument("--profile", choices=scenario.PROFILES, required=True)`;
- if `args.profile == "full"` and `RankingGroup is None`, print
  `"The full profile needs ranking groups, which this build lacks."` and
  return 1;
- create the `RankingGroup` only for `full`, collecting them into a dict
  `ranking_groups`;
- build `Contest(...)` with
  `**contest_extra_kwargs(contest_name, args.profile, ranking_groups, hasattr(Contest, "active"))`
  instead of the literal `active=True, ranking_group=ranking_group`;
- use `scenario.task_spec(spec["name"], args.profile)` and
  `score_parameters(...)` instead of the inline loop;
- after `session.commit()`, collect `contest_ids = {c.name: c.id for c in
  session.query(Contest).filter(Contest.name.in_(["loada", "loadb"]))}`
  (inside the `with`);
- write `"profile": args.profile`, `"ranked": scenario.RANKED[args.profile]`
  and `"contest_ids": contest_ids` into `users.json`, next to the existing
  keys.

- [ ] **Step 4: Run the tests**

Expected: 6 passed, plus Task 2's 7 still passing. pyflakes clean.

- [ ] **Step 5: Commit**

`test(loadtest): create the contests on both builds and per profile` (+ `Refs #7`).

---

### Task 4: Rendered config and the uniform start script

**Files:**
- Create: `cmstestsuite/loadtest/config/cms.toml.tmpl`
- Create: `cmstestsuite/loadtest/config/cms_ranking.toml.tmpl`
- Create: `cmstestsuite/loadtest/render_config.py`
- Create: `cmstestsuite/loadtest/start.sh`
- Test: `cmstestsuite/unit_tests/loadtest/render_config_test.py`

**Interfaces:**
- Produces:
  - `render_config.render(template: str, values: dict[str, str]) -> str`.
    It replaces `@NAME@` markers and raises `KeyError` naming any marker
    left without a value.
  - `render_config.worker_lines(count: int) -> str`, the TOML array body
    for `Worker = [...]`.
  - CLI: `python3 render_config.py --out-dir DIR --db-url URL --secret-key HEX --rws-password PW --workers N --cws N --two-phase {true,false}`
    writes `DIR/cms.toml` and `DIR/cms_ranking.toml`.
  - `start.sh` (inside the cms container): starts the services listed in
    `cms.toml` and blocks forever.

- [ ] **Step 1: Write the templates**

`config/cms.toml.tmpl`, built from `config/cms.sample.toml` with only
what both builds understand. `two_phase_evaluation` is fork-only, and
upstream warns about it and ignores it.

```toml
# Rendered by render_config.py for one load-test run. Both targets
# (fork and upstream cc9dfafb) read this same file.
[global]
backdoor = false
file_log_debug = false
stream_log_detailed = false
temp_dir = "/tmp"
two_phase_evaluation = @TWO_PHASE@

[services]
LogService = [["localhost", 29000]]
ResourceService = [["localhost", 28000]]
ScoringService = [["localhost", 28500]]
Checker = [["localhost", 22000]]
EvaluationService = [["localhost", 25000]]
Worker = [
@WORKERS@
]
ContestWebServer = [
@CWS_RPC@
]
AdminWebServer = [["localhost", 21100]]
ProxyService = [["localhost", 28600]]
PrometheusExporter = []
TelegramBot = []

[database]
url = "@DB_URL@"
debug = false
twophase_commit = false

[worker]
keep_sandbox = false

[sandbox]
max_file_size = 1_048_576
compilation_sandbox_max_processes = 1000
compilation_sandbox_max_time_s = 10.0
compilation_sandbox_max_memory_kib = 524_288
trusted_sandbox_max_processes = 1000
trusted_sandbox_max_time_s = 10.0
trusted_sandbox_max_memory_kib = 4_194_304

[web_server]
secret_key = "@SECRET_KEY@"
tornado_debug = false

[contest_web_server]
listen_address = [@CWS_ADDRESSES@]
listen_port = [@CWS_PORTS@]
cookie_duration = 18000
num_proxies_used = 0
submit_local_copy = false
submit_local_copy_path = "%s/submissions/"
tests_local_copy = false
tests_local_copy_path = "%s/tests/"
max_submission_length = 100_000
max_input_length = 5_000_000
docs_path = "/usr/share/cms/docs"

[admin_web_server]
listen_address = "0.0.0.0"
listen_port = 8898
cookie_duration = 36000
num_proxies_used = 0

[proxy_service]
rankings = ["http://rwsload:@RWS_PASSWORD@@ranking:8890/"]

[prometheus]
listen_address = "127.0.0.1"
listen_port = 8811
```

Check that every key above exists in `git show cc9dfafb:cms/conf.py`;
`grep` each section name. Drop any key upstream lacks, unless the fork
needs it (only `two_phase_evaluation` qualifies), and report what you
dropped.

`config/cms_ranking.toml.tmpl`:

```toml
bind_address = "0.0.0.0"
http_port = 8890
username = "rwsload"
password = "@RWS_PASSWORD@"
realm_name = "Scoreboard"
buffer_size = 100

[public]
show_id_column = false
```

- [ ] **Step 2: Write the failing renderer tests**

```python
"""Tests for the load-test config renderer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import render_config  # noqa: E402


class RenderTest(unittest.TestCase):

    def test_replaces_every_marker(self):
        self.assertEqual(
            render_config.render("a=@A@ b=@B@", {"A": "1", "B": "2"}),
            "a=1 b=2")

    def test_missing_value_names_the_marker(self):
        with self.assertRaises(KeyError) as raised:
            render_config.render("x=@MISSING@", {})
        self.assertIn("MISSING", str(raised.exception))

    def test_worker_lines(self):
        self.assertEqual(render_config.worker_lines(2),
                         '    ["localhost", 26000],\n'
                         '    ["localhost", 26001],')

    def test_full_template_renders_and_parses(self):
        import tomllib
        here = os.path.join(os.path.dirname(__file__), "..", "..",
                            "loadtest", "config")
        values = render_config.values(
            db_url="postgresql+psycopg2://cms:pw@db:5432/cmsdb",
            secret_key="0" * 32, rws_password="pw", workers=3, cws=2,
            two_phase=False)
        text = render_config.render(
            open(os.path.join(here, "cms.toml.tmpl")).read(), values)
        conf = tomllib.loads(text)
        self.assertEqual(len(conf["services"]["Worker"]), 3)
        self.assertEqual(conf["contest_web_server"]["listen_port"],
                         [8888, 8889])
        self.assertEqual(len(conf["services"]["ContestWebServer"]), 2)
        self.assertFalse(conf["global"]["two_phase_evaluation"])
        tomllib.loads(render_config.render(
            open(os.path.join(here, "cms_ranking.toml.tmpl")).read(),
            values))
```

- [ ] **Step 3: Run them to verify they fail** (module missing).

- [ ] **Step 4: Implement `render_config.py`** (license header,
  stdlib only):

```python
"""Render the load-test cms.toml and cms_ranking.toml for one run."""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = re.compile(r"@([A-Z_]+)@")


def render(template: str, values: dict[str, str]) -> str:
    """Replace every @NAME@ marker of template with values[NAME].

    raise (KeyError): if a marker has no value; the message names it.

    """
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError("no value for config marker @%s@" % name)
        return values[name]
    return MARKER.sub(substitute, template)


def worker_lines(count: int) -> str:
    """Return the body of the Worker = [...] array for count workers."""
    return "\n".join('    ["localhost", %d],' % (26000 + i)
                     for i in range(count))


def values(db_url: str, secret_key: str, rws_password: str, workers: int,
           cws: int, two_phase: bool) -> dict[str, str]:
    """Return the marker values of both templates."""
    return {
        "DB_URL": db_url,
        "SECRET_KEY": secret_key,
        "RWS_PASSWORD": rws_password,
        "WORKERS": worker_lines(workers),
        "CWS_RPC": "\n".join('    ["localhost", %d],' % (21000 + i)
                             for i in range(cws)),
        "CWS_ADDRESSES": ", ".join(['"0.0.0.0"'] * cws),
        "CWS_PORTS": ", ".join(str(8888 + i) for i in range(cws)),
        "TWO_PHASE": "true" if two_phase else "false",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--rws-password", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--cws", type=int, required=True)
    parser.add_argument("--two-phase", choices=("true", "false"),
                        required=True)
    args = parser.parse_args()
    vals = values(args.db_url, args.secret_key, args.rws_password,
                  args.workers, args.cws, args.two_phase == "true")
    os.makedirs(args.out_dir, exist_ok=True)
    for name in ("cms.toml", "cms_ranking.toml"):
        with open(os.path.join(HERE, "config", name + ".tmpl")) as f:
            text = render(f.read(), vals)
        with open(os.path.join(args.out_dir, name), "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Write `start.sh`**

Both builds use this script. It runs inside the cms container with
`CMS_CONFIG=/loadtest/run/cms.toml`, and it deliberately bypasses
supervisord, the fork's entrypoint and ResourceService autorestart, so
that both builds run the same processes the same way.

```bash
#!/usr/bin/env bash
# Start every CMS service of the load-test stack (except ProxyService,
# which run.sh starts once the contests exist) and wait forever. Used by
# both targets so that they run the same processes the same way. A
# service that dies stays dead: a crash under load is a finding.
set -euo pipefail
: "${CMS_CONFIG:?CMS_CONFIG must point to the rendered cms.toml}"
WORKERS=${LOAD_WORKER_COUNT:?}
CWS=${LOAD_CWS_COUNT:?}
LOG=/loadtest/run/start.log
start() { "$@" >>"$LOG" 2>&1 & echo "started $* (pid $!)" >>"$LOG"; }

start cmsLogService 0
sleep 2
start cmsScoringService 0 -c ALL
start cmsEvaluationService 0 -c ALL
for ((i = 0; i < WORKERS; i++)); do start cmsWorker "$i" -c ALL; done
for ((i = 0; i < CWS; i++)); do start cmsContestWebServer "$i" -c ALL; done
start cmsAdminWebServer 0 -c ALL
wait
```

Before committing, check in both images that each of these scripts accepts
`-c ALL`:

```bash
sg docker -c "docker --context default run --rm --entrypoint '' cmsload-<target>:latest cmsEvaluationService --help"
```

This works once Task 6's `build.sh` exists. If you reach this step before
then, do the check in Task 8 and note it in your report.

- [ ] **Step 6: Run the tests, lint, `bash -n start.sh`, commit**

Expected: 4 passed. Commit message:
`test(loadtest): render one config for both builds and start services uniformly` (+ `Refs #7`).

---

### Task 5: Driver and analyzer follow the profile; metrics.json

**Files:**
- Modify: `cmstestsuite/loadtest/driver.py` (`ranking_watcher`, around
  lines 319-347; the docstring line 21)
- Modify: `cmstestsuite/loadtest/analyze.py`
- Test: `cmstestsuite/unit_tests/loadtest/analyze_test.py`
- Create: `cmstestsuite/unit_tests/loadtest/fixtures/run_small/` (a tiny
  hand-written run directory)

**Interfaces:**
- Consumes: `users.json` keys `profile`, `ranked` (Task 3).
- Produces:
  - Driver: `ranking_watcher` polls `"%s/%s" % (rws_base, path)` for each
    `(contest, path)` in `users["ranked"].items()`, and records
    `contest=` in each ranking entry.
  - `analyze.rws_mismatches(task_scores: list[dict], ranking: list[dict], ranked: set[str]) -> tuple[int, list]`:
    the count of pairs checked and the mismatching `(task_score, rws_score)`
    pairs, considering only `task_scores` whose `contest` is in `ranked`.
  - `analyze.main(run_dir, project_prefix="cmsload-")` writes `summary.md`
    and `metrics.json`.
  - `metrics.json` holds one flat dict with these keys (missing values are
    `null`): `run, target, profile, users, submissions_sent,
    submissions_rejected, login_failures, http_errors, score_mismatches,
    rws_pairs, rws_mismatches, login_p50, login_p95, submit_p50,
    submit_p95, submit_end_p95, scored_p50, scored_p95, scored_max,
    drain_after_stop_s, peak_pg_connections, cpu_mean_by_container,
    mem_last_by_container`.

- [ ] **Step 1: Build the fixture and write the failing tests**

The fixture is a minimal run directory with the files that
`analyze.main()` reads: `users.json` (3 users: a001 and a002 on loada,
b001 on loadb; `profile` portable; `ranked` `{"loada": "scores"}`; start
1000.0, stop 1600.0; `target` "fork"), `requests.jsonl` (a few login and
submit entries in phases `login_burst` and `end_burst`, one with
`ok: false`), `submissions.jsonl`, `ranking.jsonl` (scores for a001 and
a002 only), `monitor.jsonl` (two samples with a `pg_connections` field
matching what `monitor.py` writes; read `monitor.py` for the exact key
names), `db_export.json` (task_scores for all 3 users, with b001's
differing from anything in ranking), and an empty `cmslog/`. Before
writing it, read `analyze.py` end to end so that every file and key it
reads is present. Keep each file under 20 lines.

```python
"""Tests for the load-test analyzer."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import analyze  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "run_small")


class AnalyzeTest(unittest.TestCase):

    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, self.run_dir, dirs_exist_ok=True)
        self.addCleanup(shutil.rmtree, self.run_dir)

    def test_rws_check_only_covers_ranked_contests(self):
        db = json.load(open(os.path.join(self.run_dir, "db_export.json")))
        ranking = [json.loads(line) for line in
                   open(os.path.join(self.run_dir, "ranking.jsonl"))]
        pairs, diff = analyze.rws_mismatches(
            db["task_scores"], ranking, {"loada"})
        self.assertEqual(diff, [])
        self.assertEqual(pairs, sum(1 for t in db["task_scores"]
                                    if t["contest"] == "loada"))

    def test_main_writes_summary_and_metrics(self):
        analyze.main(self.run_dir)
        self.assertTrue(os.path.exists(
            os.path.join(self.run_dir, "summary.md")))
        metrics = json.load(open(os.path.join(self.run_dir,
                                              "metrics.json")))
        self.assertEqual(metrics["profile"], "portable")
        self.assertEqual(metrics["rws_mismatches"], 0)
        self.assertEqual(metrics["http_errors"], 1)
        self.assertIn("login_p95", metrics)
```

- [ ] **Step 2: Run them to verify they fail.**

- [ ] **Step 3: Implement**
  - `driver.py`: in `ranking_watcher`, loop over
    `self.data["ranked"].items()` (`self.data` is the loaded `users.json`;
    check the constructor's attribute name and use it), build the URL from
    the path, and pass `contest=contest` to `self.rec.ranking(...)`. Fix the
    module docstring line accordingly.
  - `analyze.py`:
    - Extract the ranking comparison into `rws_mismatches()` with the
      signature above, and call it with `set(users["ranked"])`.
    - Pass `users["profile"]` to every `scenario` call that Task 2 changed.
    - Change the docker-stats filter to
      `d.get("Name", "").startswith(project_prefix)` and add the
      `project_prefix` parameter, defaulting to `"cmsload-"`.
    - Collect the `metrics.json` values while building the markdown, at
      the same places the numbers are formatted, so the two always agree.
      Write the file next to `summary.md` with
      `json.dump(metrics, f, indent=1, sort_keys=True)`.
    - Take `target` and `run` from `users.json` (`users.get("target")`;
      `run.sh` writes it, Task 6) and from the directory name.
    - `drain_after_stop_s` = the last `scored_at` in `db_export.json` minus
      `stop`, or `null` if nothing was scored.
    - `peak_pg_connections` = the max over `monitor.jsonl` samples.
    - Give the `__main__` block an argv option:
      `analyze.py <run_dir> [--project-prefix PREFIX]` (argparse), passed
      to `main()`. `run.sh` uses it.

- [ ] **Step 4: Run the tests**: 2 passed; earlier tests still pass;
  pyflakes clean.

- [ ] **Step 5: Commit**:
  `test(loadtest): check the ranking per profile and write metrics.json` (+ `Refs #7`).

---

### Task 6: Images, compose and the runner

**Files:**
- Create: `cmstestsuite/loadtest/build.sh`
- Modify (rewrite): `cmstestsuite/loadtest/compose.yml`,
  `cmstestsuite/loadtest/run.sh`, `cmstestsuite/loadtest/teardown.sh`

**Interfaces:**
- Consumes: `render_config.py` (Task 4), `start.sh` (Task 4),
  `setup_contest.py --profile` (Task 3), `users.json` keys
  `contest_ids`/`ranked` (Task 3), `analyze.py` (Task 5).
- Produces:
  - `build.sh <fork|upstream> <git-ref>`: builds `cmsload-<target>:latest`
    from `git archive <ref>` with that ref's `Dockerfile`, and records
    `out/images/<target>.txt` with the ref, the full sha and the toolchain
    versions (`g++ --version | head -1`, `python3 --version`,
    `javac -version`, `isolate --version`).
  - `run.sh --target T --profile P --name NAME [--users-a N] [--users-b N] [--login-window S] [--contest S] [--end-burst S] [--rate R] [--end-burst-factor F] [--workers N] [--cws N]`.
    The defaults are the 2026-09-30 full1 values: users-a 175,
    users-b 75, login-window 150, contest 1500, end-burst 300, rate 45,
    factor 1.0, workers 8, cws 2.
  - `teardown.sh <target> [--images]`.

- [ ] **Step 1: `build.sh`**

```bash
#!/usr/bin/env bash
# Build the CMS image of one load-test target from a git ref, with that
# ref's own Dockerfile, and record what was built.
#   ./build.sh fork beta
#   ./build.sh upstream cc9dfafb
set -euo pipefail
TARGET=${1:?target: fork or upstream} REF=${2:?git ref}
[[ $TARGET == fork || $TARGET == upstream ]] || { echo "unknown target $TARGET" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
SHA=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
git -C "$REPO" archive "$SHA" | tar -x -C "$CTX"
DC=(sg docker -c)
"${DC[@]}" "docker --context default build -t cmsload-$TARGET:latest '$CTX'"
if [[ $TARGET == fork ]]; then
  "${DC[@]}" "docker --context default build -t cmsload-ranking:latest -f '$CTX/docker/Dockerfile.ranking' '$CTX'"
fi
mkdir -p "$HERE/out/images"
{
  echo "target=$TARGET ref=$REF sha=$SHA built=$(date -u +%FT%TZ)"
  "${DC[@]}" "docker --context default run --rm --entrypoint '' cmsload-$TARGET:latest sh -c 'g++ --version | head -1; python3 --version; javac -version 2>&1; isolate --version | head -1'"
} | tee "$HERE/out/images/$TARGET.txt"
```

RWS runs from `cmsload-<target>:latest` for both targets
(`cmsRankingWebServer` exists in both images; read
`git show cc9dfafb:Dockerfile` to confirm the PATH). The fork's
`Dockerfile.ranking` is built only to check that it still builds; the
compose file does not use it. If that check costs more than a minute,
drop it and say so.

- [ ] **Step 2: `compose.yml`**

Keep the `db` service from the recovered file (postgres 17,
`log_min_duration_statement=1000`, `log_lock_waits=on`,
`deadlock_timeout=1s`, healthcheck) and the `driver` service. Replace
`db-init`, `ranking` and `cms` with:

```yaml
  db-init:
    image: cmsload-${LOAD_TARGET}:latest
    entrypoint: []
    environment:
      CMS_CONFIG: /loadtest/run/cms.toml
    volumes:
      - ./:/loadtest
    depends_on:
      db:
        condition: service_healthy
    command: ["cmsInitDB"]
    restart: "no"

  ranking:
    image: cmsload-${LOAD_TARGET}:latest
    entrypoint: []
    environment:
      CMS_RANKING_CONFIG: /loadtest/run/cms_ranking.toml
    volumes:
      - ./:/loadtest
    command: ["cmsRankingWebServer"]

  cms:
    image: cmsload-${LOAD_TARGET}:latest
    entrypoint: []
    environment:
      CMS_CONFIG: /loadtest/run/cms.toml
      LOAD_WORKER_COUNT: ${LOAD_WORKER_COUNT}
      LOAD_CWS_COUNT: ${LOAD_CWS_COUNT}
      TZ: UTC
    depends_on:
      db-init:
        condition: service_completed_successfully
      ranking:
        condition: service_started
    volumes:
      - ./:/loadtest
    command: ["bash", "/loadtest/start.sh"]
    privileged: true
    cgroup: host
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
```

The `db` service uses `POSTGRES_USER: cms` and
`POSTGRES_PASSWORD: ${LOAD_DB_PASSWORD}`. Remove every `build:` key: the
images come from `build.sh`. Update the header comment to say how to run
it (`run.sh`) and that the project name is `cmsload-<target>`. Check
whether `cmsRankingWebServer` honours `CMS_RANKING_CONFIG` in both builds
(`cmsranking/Config.py`). If one build does not, mount the file at that
build's default path instead and note it in a comment.

Validate with:

```bash
cd cmstestsuite/loadtest && LOAD_TARGET=fork LOAD_DB_PASSWORD=x LOAD_WORKER_COUNT=2 LOAD_CWS_COUNT=2 sg docker -c "docker --context default compose -p cmsload-fork -f compose.yml config -q" && echo ok
```

- [ ] **Step 3: `run.sh`**

Rewrite it around the recovered flow. In order:

1. **Parse the flags** with a `while/case` loop and the defaults above.
   Refuse `--profile full --target upstream` with
   `"The full profile uses fork-only features (dependencies, two-phase, ranking groups); run it on the fork target."`
   and exit 2.
2. **Refuse to start** if `docker ps` shows any container whose name
   starts with `cmsload-`, `cmsci-` or `mc2-e2e-`, or contains `stress`
   (message as in the recovered script).
3. **Set up the run.** `PROJ=cmsload-$TARGET`, `OUT=$HERE/out/$NAME`.
   Write `$HERE/.env.load` with `LOAD_TARGET`, `LOAD_DB_PASSWORD` and
   `LOAD_RWS_PASSWORD` (`python3 -c "import secrets; print(secrets.token_hex(16))"`),
   `LOAD_SECRET_KEY` (token_hex(16)), `LOAD_WORKER_COUNT` and
   `LOAD_CWS_COUNT`, with mode 600.
4. **Render the config** with `python3 "$HERE/render_config.py" --out-dir "$HERE/run" ...`,
   with `--two-phase true` only for `full`. The DB URL is
   `postgresql+psycopg2://cms:$LOAD_DB_PASSWORD@db:5432/cmsdb`.
5. **Arm the collection trap** right after `mkdir -p "$OUT"` with
   `trap collect EXIT`, so that logs are collected even if a later step
   fails. `collect` does:
   - kill the stats sampler;
   - `pkill -f monitor.py` in cms;
   - `db_export.py` into `$OUT/db_export.json` (`|| true`);
   - copy `/home/cmsuser/cms/log/.` into `$OUT/cmslog/`;
   - copy `$HERE/run/start.log`;
   - save the compose logs of cms, db and ranking;
   - copy `$HERE/run/cms.toml` (without the `secret_key` and URL lines:
     `grep -v -e secret_key -e '^url'`) to `$OUT/cms.toml`;
   - copy `out/images/$TARGET.txt` to `$OUT/image.txt`.
6. **Bring the stack up:** `compose down -v --remove-orphans`, then
   `compose up -d --wait db`, then `compose up -d ranking driver cms`.
7. **Wait for readiness**, up to 180 s. Every CWS port (8888+i), AWS 8898
   and RWS 8890 must answer HTTP. Probe from the driver container with
   `python3 -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=3)"`,
   treating any HTTP status, including 404 and 302, as up; only a
   connection error is not ready. Also echo over RPC to ES (25000), SS
   (28500) and every Worker (26000+i) with
   `python3 -c "import sys; sys.path.insert(0, '/loadtest'); import monitor; print(monitor.rpc(('localhost', int(sys.argv[1])), 'echo', {'string': 'ok'}))"`
   inside the cms container; read `monitor.rpc`'s signature and adapt the
   call. On timeout, print `start.log` and exit 1 (the trap still runs).
8. **Create the contests:** `compose exec -T cms python3 /loadtest/setup_contest.py --profile "$PROFILE" ... --out "/loadtest/out/$NAME/users.json"`,
   then add `"target": "$TARGET"` to `users.json` with a python one-liner.
9. **Start ProxyService:** for `portable`,
   `compose exec -d cms bash -c "cmsProxyService 0 -c <loada id> >>/loadtest/run/start.log 2>&1"`,
   with the id read from `users.json` `contest_ids.loada`; for `full`, the
   same without `-c`. Wait until an RPC echo on 28600 answers (60 s max).
10. **Run the monitor, the docker-stats sampler and the driver** as in the
    recovered script. The `--cws` URL list is built from the CWS count
    (`http://cms:8888 ...`), and `--rws http://ranking:8890`.
11. **Wait 15 s, then run `analyze.py`** on `$OUT` with
    `--project-prefix "$PROJ"` (add an argv option to `analyze.py`'s
    `__main__` if Task 5 did not). The trap does the rest.

`teardown.sh <target> [--images]`: `compose -p cmsload-<target> ... down -v --remove-orphans`;
with `--images`, also remove `cmsload-<target>:latest`.

- [ ] **Step 4: Static checks**

Run `bash -n` on `run.sh`, `build.sh`, `teardown.sh` and `start.sh`. Run
`shellcheck` on them if it is installed (`uvx shellcheck-py` also works).
Then run:

```bash
cmstestsuite/loadtest/run.sh --target upstream --profile full --name x; echo "exit=$?"
```

Expected: the refusal message and `exit=2`, with no Docker command run.

- [ ] **Step 5: Commit**:
  `test(loadtest): build per git ref and run both targets with one runner` (+ `Refs #7`).

---

### Task 7: compare.py

**Files:**
- Create: `cmstestsuite/loadtest/compare.py`
- Test: `cmstestsuite/unit_tests/loadtest/compare_test.py`

**Interfaces:**
- Consumes: `metrics.json` (Task 5).
- Produces:
  - `compare.load(run_dirs: list[str]) -> list[dict]`.
  - `compare.table(runs: list[dict], keys: list[str]) -> str`: a markdown
    table with one column per run and one row per key.
  - `compare.summarize(runs: list[dict], group_by: str = "target") -> list[dict]`:
    the median per group for numeric keys.
  - CLI: `python3 compare.py out/run1 out/run2 ... [--median]`.

- [ ] **Step 1: Failing tests**

```python
"""Tests for the load-test run comparison."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "loadtest"))

import compare  # noqa: E402


class CompareTest(unittest.TestCase):

    RUNS = [
        {"run": "f1", "target": "fork", "login_p95": 0.2, "http_errors": 0},
        {"run": "f2", "target": "fork", "login_p95": 0.4, "http_errors": 2},
        {"run": "u1", "target": "upstream", "login_p95": 1.5,
         "http_errors": 0},
    ]

    def test_table_has_one_column_per_run(self):
        text = compare.table(self.RUNS, ["login_p95"])
        self.assertIn("| f1 | f2 | u1 |", text)
        self.assertIn("| login_p95 | 0.2 | 0.4 | 1.5 |", text)

    def test_compare_tolerates_missing_metrics(self):
        runs = self.RUNS + [{"run": "crashed", "target": "upstream"}]
        text = compare.table(runs, ["login_p95"])
        self.assertIn("| 1.5 | - |", text)

    def test_summarize_takes_the_median_per_target(self):
        rows = compare.summarize(self.RUNS)
        fork = next(r for r in rows if r["target"] == "fork")
        self.assertAlmostEqual(fork["login_p95"], 0.3)
        self.assertEqual(fork["runs"], 2)
```

- [ ] **Step 2: Verify they fail.** **Step 3: Implement** with the stdlib
  only (`statistics.median`; format floats with `%.3g`; print `-` for a
  missing value; a key list that defaults to every `metrics.json` key
  except `run`/`target`/`profile` and the per-container dicts, in a fixed
  order). **Step 4: Tests pass, pyflakes clean.** **Step 5: Commit**
  `test(loadtest): add a side-by-side comparison of runs` (+ `Refs #7`).

---

### Task 8: README and smoke runs on both targets

**Files:**
- Create: `cmstestsuite/loadtest/README.md`
- Modify: whatever the smoke runs show is broken, in the harness only.
  Never change CMS product code in this plan: if the product misbehaves,
  write it down for Task 9's report and the issues.

- [ ] **Step 1: README**

Sections, in this order:
1. **What it measures:** the scenario, the two profiles and the two
   targets, with the comparability caveats copied from the spec.
2. **Prerequisites:** rootful Docker with `privileged` + `cgroup: host`
   (isolate), `sg docker` usage, ~16 cores recommended, and no other
   `cmsload-`/`cmsci-` stack running.
3. **Build:** `./build.sh fork beta`, `./build.sh upstream cc9dfafb`.
4. **Run:** the `run.sh` flags table with defaults, and two examples:
   smoke (`--users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10`)
   and full (the defaults).
5. **Output:** every file in `out/<name>/`, and what each `summary.md`
   section and each `metrics.json` key means.
6. **Compare:** `python3 compare.py out/a out/b --median`.
7. **Teardown.**
8. **Limits:** one host, no proxy/TLS, synthetic cheap testcases, one
   client IP, fork-only work that cannot be turned off.
9. **History:** `REPORT-2026-09-30.md` is the first run, with the
   recovered harness.

- [ ] **Step 2: Build both images**

```bash
cd /var/home/areslolxd/Documentos/cms/.worktrees/loadtest/cmstestsuite/loadtest
./build.sh fork HEAD
./build.sh upstream cc9dfafb
```

Expected: both `out/images/*.txt` list the toolchain versions. If they
differ between targets, keep going; Task 9 reports it.

- [ ] **Step 3: Smoke runs, one at a time**

```bash
./run.sh --target fork --profile portable --name smoke-fork-portable --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh fork
./run.sh --target upstream --profile portable --name smoke-upstream-portable --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh upstream
./run.sh --target fork --profile full --name smoke-fork-full --users-a 14 --users-b 6 --login-window 60 --contest 480 --end-burst 120 --rate 10
./teardown.sh fork
```

Run each in the background and wait for it; each takes about 15 minutes.
Every smoke run must show in `summary.md` and `metrics.json`:
- 20 of 20 logins;
- 0 HTTP errors (apart from `rws_scores` during startup, if any);
- 0 rejected submissions, 0 stuck submissions and 0 score mismatches;
- `rws_mismatches` = 0.

If a run fails, use superpowers:systematic-debugging, fix the harness,
rerun that smoke run, and commit each fix separately (`fix(loadtest): ...`).

- [ ] **Step 4: Check the collect trap**

Start a smoke run and press Ctrl-C (or `kill -INT` the run.sh pid) once
the driver has started. Expected: `out/<name>/cmslog/` and the compose
logs exist.

- [ ] **Step 5: Commit** the README and any fixes:
  `docs(loadtest): explain how to build, run and compare load tests` (+ `Refs #7`).

---

### Task 9: Baseline, ramp and report

**Files:**
- Create: `docs/superpowers/reports/<YYYY-MM-DD>-loadtest-baseline.md`
  (the date the report is written)

- [ ] **Step 1: Baseline (spec L10a)**

Run three times per target, alternating fork and upstream (f1, u1, f2,
u2, f3, u3) so that drift on the host affects both equally:
`./run.sh --target <t> --profile portable --name base-<t>-<n>` with the
defaults (250 users). Tear down between runs. Each run takes about 45
minutes. Run them in the background, one at a time.

- [ ] **Step 2: Fork full profile (L10b)**

Run `./run.sh --target fork --profile full --name full-fork-1`.

- [ ] **Step 3: Ramp (L10c and L10d)**

On the fork, run `portable` with `--users-a`/`--users-b` scaled to 500,
1000 and 2000 total users (70 % in loada, 30 % in loadb). Scale `--rate`
in proportion to users (45 per minute per 250 users). Stop at the first
level with any of:
- HTTP errors above 0.5 %;
- rejected submissions;
- a `drain_after_stop_s` above 900 s or none at all;
- a container out of memory.

Then run upstream up to the same level, or up to its own breaking point
if that comes earlier. Record which component saturated first at the
breaking level, judging from `docker stats` CPU per container, the
monitor's queue and PG samples, and the request latency by kind: CWS
(login, submit), ES (queue), Workers, PostgreSQL (connections, lock
waits, slow statements) or RWS.

- [ ] **Step 4: Write the report**

Sections:
1. **Setup:** host CPU and RAM, refs, image toolchains, and the scenario
   knobs.
2. **Baseline:** `compare.py --median` output over base-fork-* vs
   base-upstream-*, plus a short reading of each row that differs by
   more than 20 %.
3. **The full profile** as a third configuration, against
   base-fork-median.
4. **Ramp:** a table of users × key metrics per target, the breaking
   level, and the first component to saturate, with its evidence.
5. **Inputs for other issues:**
   - #29 (Go): what saturates first, and whether it is Python-bound.
   - #42: the peak PG connections per level and a suggested
     `max_connections`/pool size.
   - #39: whether any run showed DB stalls.
   - #5 and #6: rejected or late submissions and ES latency.
6. **Caveats:** copied from the spec, plus anything observed.

Copy the README limits instead of rewording them.

- [ ] **Step 5: Commit**:
  `docs(loadtest): report the fork vs upstream baseline and the ramp` (+ `Refs #7`).
  Do not post issue comments; the controller does that after review.
