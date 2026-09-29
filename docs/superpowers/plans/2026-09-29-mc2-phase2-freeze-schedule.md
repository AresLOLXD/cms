# Ranking Freeze and Scheduled Visibility (MC-2 phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let organizers schedule, per ranking group, when its public
scoreboard is hidden, shown, frozen and unfrozen. While frozen, the public
sees the scoreboard as it was at the freeze time, and the staff see it live.

**Architecture:** The group keeps two time windows (hidden, frozen) in the
database. ProxyService sends them to RankingWebServer (RWS) in the existing
visibility `PUT`. RWS evaluates the state from the clock on every request
(no timers): a hidden group behaves as in MC-2 minimal. For a frozen group
the public requests are served from the score history cut at the freeze
time, the live score events are dropped, and public streams are cut and
told to reload at each transition. AWS edits the windows with fixed UTC
times and "… now" buttons.

**Tech Stack:** Python 3.12. RWS runs on gevent with Werkzeug. AWS runs
Tornado with SQLAlchemy 2.0 on PostgreSQL. ProxyService is asyncio-based.
The ranking frontend is jQuery (`cmsranking/static/DataStore.js`).

**Spec:** `docs/superpowers/specs/2026-09-29-mc2-phase2-freeze-schedule-design.md`

## Global Constraints

- Implement after the 2026-10-10 contest, on `beta`. Nothing here may land
  before it.
- Window semantics (spec §1), verbatim:
  `hidden(now) = hide_at is not None and hide_at <= now and (show_at is None or now < show_at)`
  and
  `frozen(now) = freeze_at is not None and freeze_at <= now and (unfreeze_at is None or now < unfreeze_at)`.
- Hidden wins over frozen: a group that is hidden and frozen behaves as
  hidden.
- The freeze snapshot counts the score changes with `time <= freeze_at`.
  Every score change carries the submission's time; a token use carries
  the token's time.
- Time representations:
  - Database: naive UTC `DateTime`.
  - Wire (ProxyService → RWS): integer Unix seconds, or `null`.
  - RWS: compares the wire integers with `time.time()`.
- Wire format of `PUT /<group>/visibility`:
  `{"hide_at": int|null, "show_at": int|null, "freeze_at": int|null, "unfreeze_at": int|null, "staff_password": str|null}`.
  RWS also accepts MC-2 minimal's `{"hidden": bool, "staff_password": str|null}`:
  `true` means `hide_at = 0` with the other times null; `false` means all
  times null.
- RWS answers 400 when:
  - a time is not an integer (a `bool` is not an integer here);
  - an end is not after its start (both set);
  - the body mixes both formats.
- A `visibility.json` that cannot be read fails closed: hidden, with no
  staff password.
- AWS keeps writing the `hidden` column, derived as "hidden now or a hide
  pending": `hide_at` is set and `show_at` is empty or in the future.
- The Spanish user-facing strings, verbatim:
  - public banner: "Ranking congelado desde las HH:MM (<time zone name>)",
    with a link "Acceso staff";
  - staff banner: "Vista staff: ranking congelado para el público";
  - AWS buttons: "Ocultar ahora", "Mostrar ahora", "Congelar ahora",
    "Descongelar ahora".
- Deployment order: RWS first (`./up.sh` → `3) Ranking only`), then CMS
  (`./up.sh` → `4) CMS only`).
- Code style (`CLAUDE.md`): PEP 8, PEP 484, the project's docstring format,
  English code and comments, Conventional Commits.
- Tests:
  - Use `.venv/bin/pytest` only.
  - `cmstestsuite/unit_tests/cmsranking/` and `db/rankinggroup_test.py` are
    the gevent group; everything else here is the asyncio group. Never run
    both groups in one process.
  - Wrap runs in `timeout --foreground --signal=ABRT 900`.
  - DB tests use a private database: copy `.superpowers/scratch/ctrl-cms.toml`,
    rename the database, and drop it at the end.

## Review Focus

1. **A submission made exactly at `freeze_at`.** It counts in the
   snapshot, because the rule is `<=`. Task 2 pins it.
2. **A request exactly at `unfreeze_at`, or exactly at `show_at`.** The
   window is half-open, so the group is no longer frozen or hidden at that
   instant. Task 1 and Task 3 pin it.
3. **A public page that stays open across the unfreeze.** It must end up
   live, not stuck on the snapshot, even if the event cache no longer has
   the dropped score events: the reconnecting stream gets `reload`. Task 5
   pins it.
4. **A user or task with only post-freeze submissions.** It is absent from
   the public `/scores`, and its `/sublist` is empty while frozen. Task 2
   and Task 4 pin it.
5. **"Ocultar ahora" with a future "mostrar a las" already scheduled.** The
   future show time is kept, and a show time in the past is cleared, so the
   window stays valid. Task 8 pins it.

---

### Task 1: Window helpers shared by AWS and RWS

**Files:**
- Modify: `cmscommon/ranking_groups.py` (append after `is_valid_group_name`)
- Test: `cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py` (append a class)

**Interfaces:**
- Produces:
  - `window_is_open(start, end, now) -> bool`, which works for `int`,
    `float` and `datetime`, as long as `start`, `end` and `now` are of
    comparable types;
  - `check_window(start, end, what: str) -> None`, which raises
    `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
class TestWindows(unittest.TestCase):

    def test_window_is_half_open(self):
        self.assertFalse(window_is_open(10, 20, 9))
        self.assertTrue(window_is_open(10, 20, 10))
        self.assertTrue(window_is_open(10, 20, 19))
        self.assertFalse(window_is_open(10, 20, 20))

    def test_missing_start_never_opens(self):
        self.assertFalse(window_is_open(None, 20, 15))
        self.assertFalse(window_is_open(None, None, 15))

    def test_missing_end_never_closes(self):
        self.assertTrue(window_is_open(10, None, 10 ** 12))

    def test_works_with_datetimes(self):
        start = datetime(2026, 10, 10, 13, 0)
        end = datetime(2026, 10, 10, 16, 0)
        self.assertTrue(window_is_open(start, end,
                                       datetime(2026, 10, 10, 15, 59)))
        self.assertFalse(window_is_open(start, end, end))

    def test_check_window(self):
        check_window(None, None, "freeze")
        check_window(10, None, "freeze")
        check_window(None, 20, "freeze")
        check_window(10, 20, "freeze")
        for end in (10, 9):
            with self.assertRaisesRegex(ValueError, "freeze"):
                check_window(10, end, "freeze")
```

Add `from datetime import datetime` and
`from cmscommon.ranking_groups import check_window, window_is_open` to the
file's imports, keeping the existing ones.

- [ ] **Step 2: Run them to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py -q`
Expected: an ImportError on `check_window`.

- [ ] **Step 3: Implement**

```python
def window_is_open(start, end, now) -> bool:
    """Tell whether now falls in the time window [start, end).

    start: when the window opens, or None if it never does.
    end: when it closes, or None if it never does.
    now: the time to check; comparable with start and end (Unix
        seconds, or naive UTC datetimes).

    return: True if start <= now < end, a missing end being +infinity.

    """
    return start is not None and start <= now \
        and (end is None or now < end)


def check_window(start, end, what: str) -> None:
    """Check that a time window ends after it starts.

    start: when the window opens, or None.
    end: when it closes, or None.
    what: the name of the window, for the error message.

    raise (ValueError): if both ends are set and end <= start.

    """
    if start is not None and end is not None and end <= start:
        raise ValueError("The %s window must end after it starts." % what)
```

- [ ] **Step 4: Run the tests to see them pass**

Run the same command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add cmscommon/ranking_groups.py cmstestsuite/unit_tests/cmscommon/ranking_groups_test.py
git commit -m "feat(ranking): add the time-window helpers of scheduled visibility"
```

---

### Task 2: Scores, history and submissions at a given time (RWS)

**Files:**
- Modify: `cmsranking/Scoring.py`:
  - class `Score` (methods after `get_score`);
  - class `ScoringStore` (methods after `get_submissions`, and
    `get_global_history`).
- Test (create): `cmstestsuite/unit_tests/cmsranking/test_scoring_at.py`

**Interfaces:**
- Produces:
  - `Score.score_at(t: int) -> float`;
  - `Score.submissions_at(t: int) -> dict[str, Submission]`;
  - `ScoringStore.get_scores_at(t: int) -> dict[str, dict[str, float]]`,
    containing only scores > 0, like `ScoreHandler` today;
  - `ScoringStore.get_submissions_at(user: str, task: str, t: int) -> dict[str, Submission]`;
  - `ScoringStore.get_global_history(until: int | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the scores of RWS as they were at a given time."""

import unittest

from cmscommon.constants import SCORE_MODE_MAX
from cmsranking.Scoring import Score
from cmsranking.Subchange import Subchange
from cmsranking.Submission import Submission


def submission(user: str, task: str, time: int) -> Submission:
    sub = Submission()
    sub.user, sub.task, sub.time = user, task, time
    return sub


def change(key: str, sub_key: str, time: int, score: float | None = None,
           token: bool | None = None) -> Subchange:
    ch = Subchange()
    ch.key, ch.submission, ch.time = key, sub_key, time
    ch.score, ch.token, ch.extra = score, token, None
    return ch


class TestScoreAt(unittest.TestCase):

    def setUp(self):
        self.score = Score(SCORE_MODE_MAX)
        self.score.create_submission("s1", submission("u", "t", 100))
        self.score.create_subchange("c1", change("c1", "s1", 100, 30.0))
        self.score.create_submission("s2", submission("u", "t", 200))
        self.score.create_subchange("c2", change("c2", "s2", 200, 80.0))

    def test_score_before_and_after_each_change(self):
        self.assertEqual(self.score.score_at(99), 0.0)
        self.assertEqual(self.score.score_at(100), 30.0)
        self.assertEqual(self.score.score_at(199), 30.0)
        self.assertEqual(self.score.score_at(200), 80.0)
        self.assertEqual(self.score.score_at(10 ** 12), 80.0)

    def test_late_evaluation_of_an_early_submission_counts(self):
        # The change carries the submission's time, not the evaluation's.
        self.score.create_submission("s3", submission("u", "t", 150))
        self.score.create_subchange("c3", change("c3", "s3", 150, 50.0))
        self.assertEqual(self.score.score_at(160), 50.0)

    def test_submissions_at_hides_later_submissions_and_results(self):
        subs = self.score.submissions_at(150)
        self.assertEqual(set(subs), {"s1"})
        self.assertEqual(subs["s1"].score, 30.0)
        # The live objects are not modified.
        self.assertEqual(self.score._submissions["s2"].score, 80.0)

    def test_submissions_at_ignores_a_later_token(self):
        self.score.create_subchange(
            "c4", change("c4", "s1", 300, token=True))
        self.assertIs(self.score.submissions_at(250)["s1"].token, False)
        self.assertIs(self.score.submissions_at(300)["s1"].token, True)
```

Also add a store-level test that builds the stores the way
`build_ranking_app` does. It asserts that a user whose only submission is
after `t` has no entry in `get_scores_at(t)`, and that
`get_global_history(until=t)` has no entry with time > `t`:

```python
import os
import shutil
import tempfile

from cmsranking.Contest import Contest
from cmsranking.Scoring import ScoringStore
from cmsranking.Store import Store
from cmsranking.Task import Task
from cmsranking.Team import Team
from cmsranking.User import User


class TestScoringStoreAt(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        stores = {}
        stores["subchange"] = Store(Subchange, os.path.join(tmp, "subchanges"), stores)
        stores["submission"] = Store(Submission, os.path.join(tmp, "submissions"), stores, [stores["subchange"]])
        stores["user"] = Store(User, os.path.join(tmp, "users"), stores, [stores["submission"]])
        stores["team"] = Store(Team, os.path.join(tmp, "teams"), stores, [stores["user"]])
        stores["task"] = Store(Task, os.path.join(tmp, "tasks"), stores, [stores["submission"]])
        stores["contest"] = Store(Contest, os.path.join(tmp, "contests"), stores, [stores["task"]])
        self.scoring = ScoringStore(stores)
        stores["contest"].create("c", {"name": "C", "begin": 0, "end": 10 ** 6, "score_precision": 0})
        stores["task"].create("t", {"name": "T", "short_name": "t", "contest": "c", "order": 0,
                                    "max_score": 100.0, "extra_headers": [], "score_precision": 0,
                                    "score_mode": "max"})
        for u in ("early", "late"):
            stores["user"].create(u, {"f_name": u, "l_name": u, "team": None})
        stores["submission"].create("s1", {"user": "early", "task": "t", "time": 100})
        stores["subchange"].create("c1", {"submission": "s1", "time": 100, "score": 40.0})
        stores["submission"].create("s2", {"user": "late", "task": "t", "time": 300})
        stores["subchange"].create("c2", {"submission": "s2", "time": 300, "score": 90.0})

    def test_scores_at(self):
        self.assertEqual(self.scoring.get_scores_at(200), {"early": {"t": 40.0}})
        self.assertEqual(self.scoring.get_scores_at(300),
                         {"early": {"t": 40.0}, "late": {"t": 90.0}})

    def test_history_until(self):
        self.assertEqual([h[2] for h in self.scoring.get_global_history(until=200)], [100])
        self.assertEqual(len(list(self.scoring.get_global_history())), 2)

    def test_submissions_at(self):
        self.assertEqual(self.scoring.get_submissions_at("late", "t", 200), {})
        self.assertEqual(set(self.scoring.get_submissions_at("early", "t", 200)), {"s1"})
```

Before running, check the exact entity field names in
`cmsranking/Task.py`, `User.py` and `Contest.py` (their `validate()`), and
adjust the dictionaries above if a required field differs.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_scoring_at.py -q`
Expected: AttributeError: `score_at`, `submissions_at` and `get_scores_at`
don't exist.

- [ ] **Step 3: Implement.** In `Score`, after `get_score`:

```python
    def score_at(self, t: int) -> float:
        """Return the score as it was at time t.

        t: a Unix time; the changes with a time <= t count.

        return: the last score in the history at or before t.

        """
        score = 0.0
        for change_time, value in self._history:
            if change_time > t:
                break
            score = value
        return score

    def submissions_at(self, t: int) -> dict[str, Submission]:
        """Return the submissions and their results as they were at t.

        t: a Unix time; the submissions and changes with a time <= t
            count.

        return: copies of the submissions made at or before t, with the
            score, token and extra that the changes up to t gave them.

        """
        result: dict[str, Submission] = dict()
        for key, sub in self._submissions.items():
            if sub.time <= t:
                past = copy.copy(sub)
                past.score, past.token, past.extra = 0.0, False, list()
                result[key] = past
        for change in self._changes:
            if change.time > t:
                break
            past = result.get(change.submission)
            if past is None:
                continue
            if change.score is not None:
                past.score = change.score
            if change.token is not None:
                past.token = change.token
            if change.extra is not None:
                past.extra = change.extra
        return result
```

Add `import copy` at the top of the file. `_changes` is sorted by time
(`create_subchange` keeps it sorted), which is why the loop can `break`.

In `ScoringStore`, after `get_submissions`:

```python
    def get_scores_at(self, t: int) -> dict[str, dict[str, float]]:
        """Return the positive scores of every user and task at time t.

        t: a Unix time; the changes with a time <= t count.

        return: user -> task -> score, only for scores above zero, like
            the live /scores.

        """
        result: dict[str, dict[str, float]] = dict()
        for user, tasks in self._scores.items():
            for task, score_obj in tasks.items():
                score = score_obj.score_at(t)
                if score > 0.0:
                    result.setdefault(user, dict())[task] = score
        return result

    def get_submissions_at(
        self, user: str, task: str, t: int
    ) -> dict[str, Submission]:
        """Return the submissions of a user for a task as they were at t.

        user: the user key.
        task: the task key.
        t: a Unix time.

        return: see Score.submissions_at.

        """
        if user not in self._scores or task not in self._scores[user]:
            return dict()
        return self._scores[user][task].submissions_at(t)
```

Change the signature of `get_global_history` to
`def get_global_history(self, until: int | None = None) -> Generator[tuple[str, str, int, float]]:`
and document the new parameter in its docstring:
"until: if not None, only the entries with a time <= until." In the loop,
right after `heapq.heappop`, add:

```python
            if until is not None and time > until:
                # The queue pops in time order: nothing later counts.
                break
```

- [ ] **Step 4: Run the tests to see them pass**

Run the same command (expected: PASS). Then run all of
`cmstestsuite/unit_tests/cmsranking/`, expecting no regressions.

- [ ] **Step 5: Commit**

```bash
git add cmsranking/Scoring.py cmstestsuite/unit_tests/cmsranking/test_scoring_at.py
git commit -m "feat(rws): compute scores, history and submissions as of a time"
```

---

### Task 3: Scheduled visibility settings in RWS

**Files:**
- Modify: `cmsranking/visibility.py`:
  - add the `VisibilitySettings` dataclass and `parse_settings`;
  - rework `VisibilityState` (lines 134-194);
  - make `VisibilityGuard` use the clock for `hidden` (lines 273-553);
  - rewrite `_update` (lines 527-553).
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`, adding the
  classes `TestVisibilitySettings` and `TestScheduledHiding`. The existing
  tests must keep passing unchanged, since they use the old wire format
  and `state.update(hidden, staff_password)`.

**Interfaces:**
- Consumes: `window_is_open`, `check_window` (Task 1).
- Produces:

```python
@dataclasses.dataclass(frozen=True)
class VisibilitySettings:
    hide_at: int | None = None
    show_at: int | None = None
    freeze_at: int | None = None
    unfreeze_at: int | None = None
    staff_password: str | None = None
    def hidden(self, now: float) -> bool: ...
    def frozen(self, now: float) -> bool: ...      # False while hidden
    def boundaries(self) -> list[int]: ...          # the non-None times
    def to_json(self) -> dict: ...                  # the new wire format

HIDDEN_SINCE_ALWAYS = VisibilitySettings(hide_at=0)

def parse_settings(data: object) -> VisibilitySettings: ...  # raises ValueError

class VisibilityState:
    settings: VisibilitySettings
    secret: str
    changed_at: float            # time.time() of the last change (load time at start)
    def update_settings(self, settings: VisibilitySettings) -> bool: ...  # True if changed
    def update(self, hidden: bool, staff_password: str | None): ...      # old format, kept
    @property
    def hidden(self) -> bool: ...          # settings.hidden(time.time())
    @property
    def staff_password(self) -> str | None: ...
    def last_change(self, now: float) -> float: ...
        # max(changed_at, the largest boundary <= now)
```

- [ ] **Step 1: Write the failing tests**

```python
from cmsranking.visibility import HIDDEN_SINCE_ALWAYS, VisibilitySettings, \
    parse_settings


class TestVisibilitySettings(unittest.TestCase):

    def test_new_format(self):
        s = parse_settings({"hide_at": 10, "show_at": 20, "freeze_at": None,
                            "unfreeze_at": None, "staff_password": STAFF_HASH})
        self.assertEqual((s.hide_at, s.show_at, s.staff_password),
                         (10, 20, STAFF_HASH))
        self.assertTrue(s.hidden(10))
        self.assertFalse(s.hidden(20))

    def test_old_format(self):
        self.assertEqual(parse_settings({"hidden": True, "staff_password": None}),
                         HIDDEN_SINCE_ALWAYS)
        self.assertEqual(parse_settings({"hidden": False, "staff_password": None}),
                         VisibilitySettings())

    def test_hidden_wins_over_frozen(self):
        s = VisibilitySettings(hide_at=10, freeze_at=5)
        self.assertTrue(s.frozen(7))
        self.assertFalse(s.frozen(12))
        self.assertTrue(s.hidden(12))

    def test_rejects(self):
        for body in [
                {"hide_at": True, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": 1.5, "show_at": None, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": 20, "show_at": 20, "freeze_at": None,
                 "unfreeze_at": None, "staff_password": None},
                {"hide_at": None, "show_at": None, "freeze_at": 30,
                 "unfreeze_at": 10, "staff_password": None},
                {"hidden": True, "hide_at": 3, "staff_password": None},
                {"hide_at": None, "staff_password": None},
                []]:
            with self.assertRaises(ValueError, msg=body):
                parse_settings(body)

    def test_last_change(self):
        state = VisibilityState(tempfile.mkdtemp())
        state.update_settings(VisibilitySettings(freeze_at=100,
                                                 unfreeze_at=200))
        state.changed_at = 50
        self.assertEqual(state.last_change(99), 50)
        self.assertEqual(state.last_change(150), 100)
        self.assertEqual(state.last_change(250), 200)


class TestScheduledHiding(VisibilityTestCase):

    def put_settings(self, group: str, **times):
        body = {"hide_at": None, "show_at": None, "freeze_at": None,
                "unfreeze_at": None, "staff_password": STAFF_HASH}
        body.update(times)
        return self.client.put("/%s/visibility" % group,
                               data=json.dumps(body),
                               content_type="application/json", headers=AUTH)

    def test_hidden_only_inside_the_window(self):
        self.put_contest("/olim")
        self.assertEqual(self.put_settings("olim", hide_at=100,
                                           show_at=200).status_code, 204)
        with patch("cmsranking.visibility.time.time", return_value=99):
            self.assertEqual(self.client.get("/olim/contests/").status_code, 200)
        with patch("cmsranking.visibility.time.time", return_value=100):
            self.assertEqual(self.client.get("/olim/contests/").status_code, 403)
        with patch("cmsranking.visibility.time.time", return_value=200):
            self.assertEqual(self.client.get("/olim/contests/").status_code, 200)

    def test_settings_survive_a_restart(self):
        self.put_contest("/olim")
        self.put_settings("olim", hide_at=100, show_at=200)
        self.client = self.make_client()
        with patch("cmsranking.visibility.time.time", return_value=150):
            self.assertEqual(self.client.get("/olim/contests/").status_code, 403)

    def test_old_file_is_read(self):
        group_dir = os.path.join(self.lib_dir, "groups", "olim")
        os.makedirs(group_dir)
        with open(os.path.join(group_dir, VISIBILITY_FILE), "w") as f:
            json.dump({"hidden": True, "staff_password": None,
                       "secret": "ab" * 32}, f)
        state = VisibilityState(group_dir)
        self.assertEqual(state.settings, HIDDEN_SINCE_ALWAYS)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_visibility.py -q -k "Settings or Scheduled"`
Expected: an ImportError on `VisibilitySettings`.

- [ ] **Step 3: Implement.** In `cmsranking/visibility.py`:

Add `import dataclasses` and `import time` to the imports, and
`from cmscommon.ranking_groups import check_window, window_is_open`.

```python
TIME_FIELDS = ("hide_at", "show_at", "freeze_at", "unfreeze_at")


@dataclasses.dataclass(frozen=True)
class VisibilitySettings:
    """The visibility windows of a group and its staff password.

    The times are Unix seconds, or None: the group is hidden during
    [hide_at, show_at) and frozen during [freeze_at, unfreeze_at), and
    hidden wins over frozen.

    """
    hide_at: int | None = None
    show_at: int | None = None
    freeze_at: int | None = None
    unfreeze_at: int | None = None
    staff_password: str | None = None

    def hidden(self, now: float) -> bool:
        return window_is_open(self.hide_at, self.show_at, now)

    def frozen(self, now: float) -> bool:
        return not self.hidden(now) and \
            window_is_open(self.freeze_at, self.unfreeze_at, now)

    def boundaries(self) -> list[int]:
        return [t for t in (self.hide_at, self.show_at, self.freeze_at,
                            self.unfreeze_at) if t is not None]

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


# What an unreadable state file and an old {"hidden": true} both mean.
HIDDEN_SINCE_ALWAYS = VisibilitySettings(hide_at=0)


def parse_settings(data: object) -> VisibilitySettings:
    """Validate the settings sent by ProxyService, in either format.

    data: the decoded JSON: the new format (the four times and
        staff_password) or MC-2 minimal's (hidden and staff_password).

    return: the settings.

    raise (ValueError): if data is neither format, a time is not an
        integer, a window ends before it starts, or the password is not
        a valid authentication string.

    """
    if not isinstance(data, dict):
        raise ValueError("The settings must be an object.")
    staff_password = data.get("staff_password", ())
    if staff_password == ():
        raise ValueError("staff_password is missing.")
    if staff_password is not None:
        if not isinstance(staff_password, str):
            raise ValueError("staff_password must be a string or null.")
        parse_authentication(staff_password)
    old = "hidden" in data
    new = any(field in data for field in TIME_FIELDS)
    if old == new:
        raise ValueError("Send either hidden or the four times.")
    if old:
        if not isinstance(data["hidden"], bool):
            raise ValueError("hidden must be a boolean.")
        base = HIDDEN_SINCE_ALWAYS if data["hidden"] else VisibilitySettings()
        return dataclasses.replace(base, staff_password=staff_password)
    times = dict()
    for field in TIME_FIELDS:
        if field not in data:
            raise ValueError("%s is missing." % field)
        value = data[field]
        # bool is a subclass of int, and True must not mean 1970.
        if value is not None and (isinstance(value, bool)
                                  or not isinstance(value, int)):
            raise ValueError("%s must be an integer or null." % field)
        times[field] = value
    check_window(times["hide_at"], times["show_at"], "hide")
    check_window(times["freeze_at"], times["unfreeze_at"], "freeze")
    return VisibilitySettings(staff_password=staff_password, **times)
```

Replace `VisibilityState` with:

```python
class VisibilityState:
    """The visibility settings of one group, stored in its directory.

    """

    def __init__(self, group_dir: str):
        self.path = os.path.join(group_dir, VISIBILITY_FILE)
        self.settings = VisibilitySettings()
        self.secret = secrets.token_hex(32)
        # A restart counts as a change: streams opened before it reload.
        self.changed_at = time.time()
        self._load()

    @property
    def hidden(self) -> bool:
        return self.settings.hidden(time.time())

    @property
    def staff_password(self) -> str | None:
        return self.settings.staff_password

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            secret = data.pop("secret")
            if not isinstance(secret, str) or secret == "":
                raise ValueError("Wrong secret.")
            bytes.fromhex(secret)
            settings = parse_settings(data)
        except FileNotFoundError:
            # A group that was never configured is visible. Any other
            # failure to read the file must not make it public: checking
            # for the file first would take an unreadable one for none.
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            logger.error("Cannot read %s: hiding the ranking until its "
                         "visibility is sent again.", self.path,
                         exc_info=True)
            self.settings = HIDDEN_SINCE_ALWAYS
            return
        self.settings = settings
        self.secret = secret

    def update_settings(self, settings: VisibilitySettings) -> bool:
        """Replace the settings, storing them atomically first.

        settings: the new settings.

        return: whether they differ from the previous ones.

        """
        data = dict(settings.to_json(), secret=self.secret)
        directory = os.path.dirname(self.path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".vis-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        changed = settings != self.settings
        # Only the windows change what the public sees: a new staff
        # password must not make every public page reload.
        view_changed = dataclasses.replace(settings, staff_password=None) \
            != dataclasses.replace(self.settings, staff_password=None)
        self.settings = settings
        if view_changed:
            self.changed_at = time.time()
        return changed

    def update(self, hidden: bool, staff_password: str | None):
        """Replace the settings from MC-2 minimal's format.

        hidden: whether the public scoreboard is hidden.
        staff_password: the staff authentication string, or None.

        """
        self.update_settings(parse_settings(
            {"hidden": hidden, "staff_password": staff_password}))

    def last_change(self, now: float) -> float:
        """Return when the public view last changed, as of now.

        now: the current Unix time.

        return: the latest of the last settings change and the scheduled
            times already passed.

        """
        passed = [t for t in self.settings.boundaries() if t <= now]
        return max([self.changed_at] + passed)
```

The file is always written in the new format; a file written by the old
code is read through `parse_settings` in the old format.

In `VisibilityGuard.__call__`, compute `now = time.time()` once and
`hidden = self.state.settings.hidden(now)`, and replace every
`self.state.hidden` inside `__call__` with `hidden`. `_CutWhenHidden`
and `guarded_write` keep reading `self.state.hidden`: that property
re-evaluates the clock at each chunk, which is exactly what they need.

Rewrite `_update`:

```python
    def _update(self, request: Request) -> Response:
        if not self._writer_authorized(request):
            logger.warning("Unauthorized visibility update.",
                           extra={"location": request.url})
            return self._unauthorized()
        try:
            settings = parse_settings(
                json.loads(request.get_data(as_text=True)))
        except (ValueError, TypeError) as error:
            logger.warning("Bad visibility update: %s.", error)
            return Response(str(error), status=400, mimetype="text/plain")
        # ProxyService sends the settings again at each sweep: only a
        # change is worth an INFO line.
        changed = self.state.update_settings(settings)
        logger.log(logging.INFO if changed else logging.DEBUG,
                   "Ranking group %s visibility is now %s.", self.group,
                   json.dumps({k: v for k, v in settings.to_json().items()
                               if k != "staff_password"}))
        return Response(status=204)
```

`json.loads` raises `json.JSONDecodeError`, which is a subclass of
`ValueError`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/ -q`
Expected: PASS, including every existing test in `test_visibility.py`.
If an existing test asserts the old log text ("is now hidden"), update
only that string, and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add cmsranking/visibility.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
git commit -m "feat(rws): keep the scheduled visibility windows of a group"
```

---

### Task 4: The frozen public view in RWS

**Files:**
- Modify: `cmsranking/visibility.py`:
  - the `NOTICE_TEMPLATE` message placeholder, and the banners;
  - `VisibilityGuard.__call__`, `_with_banner`, `_login`, `_notice`;
  - a new `FREEZE_AT_ENVIRON` constant.
- Modify: `cmsranking/RankingWebServer.py`: `SubListHandler.wsgi_app`,
  `HistoryHandler.wsgi_app`, `ScoreHandler.wsgi_app`.
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`, adding
  `TestFrozenPublicView`.

**Interfaces:**
- Consumes: `VisibilitySettings.frozen` (Task 3), and
  `ScoringStore.get_scores_at`, `get_submissions_at`,
  `get_global_history(until=)` (Task 2).
- Produces:
  - `FREEZE_AT_ENVIRON = "cmsranking.freeze_at"`, a WSGI environ key that
    the guard sets to the freeze time for public requests to a frozen
    group;
  - handlers that honour that key;
  - `FROZEN_ROUTES`, a dict from the first path segment to `"pass"`,
    `"filter"` or `"forbid"`.

- [ ] **Step 1: Write the failing tests**

```python
class TestFrozenPublicView(VisibilityTestCase):

    TASK = {"name": "T", "short_name": "t", "contest": "c1", "order": 0,
            "max_score": 100.0, "extra_headers": [], "score_precision": 0,
            "score_mode": "max"}

    def setUp(self):
        super().setUp()
        self.put_contest("/olim")
        for path, data in [
                ("tasks/", {"t": self.TASK}),
                ("users/", {"early": {"f_name": "E", "l_name": "E", "team": None},
                            "late": {"f_name": "L", "l_name": "L", "team": None}}),
                ("submissions/", {"s1": {"user": "early", "task": "t", "time": 100},
                                  "s2": {"user": "late", "task": "t", "time": 300}}),
                ("subchanges/", {"c1": {"submission": "s1", "time": 100, "score": 40.0},
                                 "c2": {"submission": "s2", "time": 300, "score": 90.0}})]:
            self.assertEqual(self.client.put(
                "/olim/" + path, data=json.dumps(data),
                content_type="application/json", headers=AUTH).status_code, 204)
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 200,
            "unfreeze_at": 400, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)

    def at(self, now):
        return patch("cmsranking.visibility.time.time", return_value=now)

    def test_public_scores_are_the_snapshot(self):
        with self.at(350):
            response = self.client.get("/olim/scores")
        self.assertEqual(response.json, {"early": {"t": 40.0}})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_public_history_and_sublist_are_cut(self):
        with self.at(350):
            self.assertEqual([h[2] for h in self.client.get("/olim/history").json], [100])
            self.assertEqual(self.client.get("/olim/sublist/late").json, [])

    def test_raw_stores_are_forbidden(self):
        with self.at(350):
            for path in ("submissions/", "subchanges/", "submissions/s2"):
                self.assertEqual(self.client.get("/olim/" + path).status_code, 403)

    def test_other_data_passes(self):
        with self.at(350):
            for path in ("contests/", "tasks/", "users/", "config"):
                self.assertEqual(self.client.get("/olim/" + path).status_code, 200, path)

    def test_staff_see_it_live(self):
        cookie = staff_cookie_value(self.client.application.apps["olim"].state.secret,
                                    "olim", STAFF_HASH)
        with self.at(350):
            response = self.client.get(
                "/olim/scores", headers={"Cookie": "%s=%s" % (STAFF_COOKIE, cookie)})
        self.assertEqual(response.json, {"early": {"t": 40.0}, "late": {"t": 90.0}})

    def test_unfrozen_at_the_exact_end(self):
        with self.at(400):
            self.assertEqual(self.client.get("/olim/scores").json,
                             {"early": {"t": 40.0}, "late": {"t": 90.0}})

    def test_banners_and_staff_login_page(self):
        with self.at(350):
            page = self.client.get("/olim/").get_data(as_text=True)
            login = self.client.get("/olim/staff-login")
        self.assertIn("Ranking congelado desde las", page)
        self.assertIn('href="staff-login"', page)
        self.assertEqual(login.status_code, 200)
        self.assertIn('name="password"', login.get_data(as_text=True))

    def test_staff_login_works_while_frozen(self):
        with self.at(350):
            response = self.client.post("/olim/staff-login",
                                        data={"password": "s3cret"})
        self.assertEqual(response.status_code, 303)

    def test_every_route_is_classified(self):
        # A new data endpoint must be classified before it can leak.
        app = build_ranking_app(self.config, os.path.join(self.tmp, "x"), self.web_dir)
        dispatcher = app.app          # SharedDataMiddleware -> DispatcherMiddleware
        mounts = {m.strip("/").split("/")[0] for m in dispatcher.mounts}
        routes = {r.rule.strip("/").split("/")[0]
                  for r in dispatcher.app.router.iter_rules()}
        self.assertLessEqual(mounts | routes, set(FROZEN_ROUTES) | {""})
```

Import `FROZEN_ROUTES` from `cmsranking.visibility`. `STAFF_HASH` is
`build_password("s3cret", "plaintext")` in this file, so the login test
posts `"s3cret"`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_visibility.py -q -k Frozen`
Expected: FAIL. The snapshot is not applied, and `FROZEN_ROUTES` doesn't
exist yet.

- [ ] **Step 3: Implement the handlers** (`cmsranking/RankingWebServer.py`).
Import `FREEZE_AT_ENVIRON` from `cmsranking.visibility`.

In `ScoreHandler.wsgi_app`, replace the loop that builds `result` with:

```python
        freeze_at = environ.get(FREEZE_AT_ENVIRON)
        if freeze_at is not None:
            result = self.scoring_store.get_scores_at(freeze_at)
        else:
            result: dict[str, dict[str, float]] = dict()
            for u_id, tasks in self.scoring_store._scores.items():
                for t_id, score in tasks.items():
                    if score.get_score() > 0.0:
                        result.setdefault(u_id, dict())[t_id] = \
                            score.get_score()
```

In `HistoryHandler.wsgi_app`:

```python
        result = list(self.scoring_store.get_global_history(
            until=environ.get(FREEZE_AT_ENVIRON)))
```

In `SubListHandler.wsgi_app`, inside the task loop:

```python
        freeze_at = environ.get(FREEZE_AT_ENVIRON)
        for task_id in self.task_store._store.keys():
            if freeze_at is None:
                subs = self.scoring_store.get_submissions(
                    args["user_id"], task_id)
            else:
                subs = self.scoring_store.get_submissions_at(
                    args["user_id"], task_id, freeze_at)
            result.extend(subs.values())
```

- [ ] **Step 4: Implement the guard** (`cmsranking/visibility.py`).

Constants:

```python
# The environ key through which the guard tells the handlers to serve a
# frozen group's data as it was at this Unix time.
FREEZE_AT_ENVIRON = "cmsranking.freeze_at"

# What a public request to a frozen group gets, by first path segment.
# Every route of the namespace app must be listed (a test checks it):
# "filter" is served as of the freeze time, "forbid" is refused, "pass"
# carries nothing that changes after the freeze (the static files are
# the default, "").
FROZEN_ROUTES = {
    "": "pass", "contests": "pass", "tasks": "pass", "teams": "pass",
    "users": "pass", "faces": "pass", "flags": "pass", "logo": "pass",
    "config": "pass", "events": "pass",
    "scores": "filter", "history": "filter", "sublist": "filter",
    "submissions": "forbid", "subchanges": "forbid",
}

HIDDEN_MESSAGE = "Este ranking está oculto por ahora."
FROZEN_LOGIN_MESSAGE = "Acceso del staff al ranking en vivo."
```

In `NOTICE_TEMPLATE`, replace the line
`<p>Este ranking está oculto por ahora.</p>` with `<p>{message}</p>`.
Give `_notice` a `message: str = HIDDEN_MESSAGE` parameter and pass it to
`format`.

Turn `STAFF_BANNER` into a builder, keeping its CSS unchanged:

```python
def _banner(text_html: str) -> bytes:
    """Build a bottom bar with the given text (see STAFF_BANNER's CSS)."""
    return (
        '<style>'
        ':root{--rws-banner:2.25rem}'
        '@media(max-width:30em){:root{--rws-banner:3.5rem}}'
        '#InnerFrame,#UserDetail_bg{bottom:var(--rws-banner)}'
        '#SidePanel{bottom:calc(30px + var(--rws-banner))}'
        '</style>'
        '<div style="position:fixed;bottom:0;left:0;right:0;z-index:1000;'
        'box-sizing:border-box;height:var(--rws-banner);overflow:hidden;'
        'padding:0.5rem;font:0.85rem/1.25rem sans-serif;'
        'background:#b00020;color:#fff;text-align:center;">'
        + text_html + '</div>').encode("utf-8")


LINK = '<a style="color:#fff" href="%s">%s</a>'
STAFF_BANNER = _banner("Vista staff: este ranking está oculto al público "
                       "&middot; " + LINK % ("staff-logout", "Salir"))
STAFF_FROZEN_BANNER = _banner(
    "Vista staff: ranking congelado para el público &middot; "
    + LINK % ("staff-logout", "Salir"))


def public_frozen_banner(freeze_at: int) -> bytes:
    """Build the public bar of a frozen group.

    freeze_at: the freeze time, in Unix seconds.

    return: the bar, with the time in the server's local time zone.

    """
    when = datetime.fromtimestamp(freeze_at).astimezone()
    return _banner("Ranking congelado desde las %s (%s) &middot; %s" % (
        when.strftime("%H:%M"), when.strftime("%Z"),
        LINK % ("staff-login", "Acceso staff")))
```

Add `from datetime import datetime`. Give
`_with_banner(self, environ, start_response, banner: bytes = STAFF_BANNER)`
a `banner` parameter, and use it in place of `STAFF_BANNER` in the `sub`
call. Give `_login` a `message: str = HIDDEN_MESSAGE` parameter, and pass
it to `self._notice(error=True, status=401, message=message)`.

Replace `__call__` with this complete method. It keeps every MC-2
minimal behaviour of the hidden and visible states, and adds the frozen
one:

```python
    def __call__(self, environ, start_response):
        request = Request(environ)
        path = request.path
        now = time.time()
        hidden = self.state.settings.hidden(now)
        frozen = self.state.settings.frozen(now)
        start_response = self._guard_writes(request, start_response)
        if path == "/visibility" and request.method == "PUT":
            return self._update(request)(environ, start_response)
        if path == "/staff-logout" and request.method == "GET":
            return self._logout()(environ, start_response)
        if not hidden and path in INDEX_PATHS and \
                request.method in ("GET", "HEAD"):
            # The index page has a Last-Modified and nothing else (or, by
            # its file name, a max-age of 12 hours), so a browser would
            # keep it without asking, and show the scoreboard instead of
            # the notice once the group is hidden.
            start_response = _cache_control(start_response, REVALIDATE)
        if self._is_staff(request):
            if hidden or frozen:
                if path == "/" and request.method == "GET":
                    return self._with_banner(
                        environ, start_response,
                        STAFF_BANNER if hidden else STAFF_FROZEN_BANNER)
                # A cache shared with the public must not keep this.
                start_response = _cache_control(
                    start_response, PRIVATE_NO_STORE)
            return self.app(environ, start_response)
        if not hidden and not frozen:
            return _CutWhenHidden(self.app(environ, start_response),
                                  self.state, start_response)
        if request.method in ("PUT", "DELETE"):
            # The store handlers check the proxy's credentials too, but
            # after they look the key up, so an anonymous DELETE would
            # tell which keys exist. Static files would take a PUT.
            if not self._writer_authorized(request):
                logger.warning("Unauthorized request.",
                               extra={"location": request.url})
                return self._unauthorized()(environ, start_response)
            return self.app(environ, start_response)
        if frozen:
            return self._frozen(request, environ, start_response)
        if path == "/" and request.method in ("GET", "HEAD"):
            return self._notice()(environ, start_response)
        if path == "/staff-login" and request.method == "POST":
            return self._login(request, start_response)(
                environ, start_response)
        return Response("Este ranking está oculto.", status=403,
                        mimetype="text/plain",
                        headers=NO_STORE)(environ, start_response)
```

Then add:

```python
    def _frozen(self, request: Request, environ, start_response):
        """Serve a public request to a frozen group.

        request: the request being served.
        environ: the WSGI environ.
        start_response: the WSGI start_response callable.

        return: the body of the response.

        """
        path = request.path
        if path == "/staff-login":
            if request.method == "POST":
                return self._login(request, start_response,
                                   FROZEN_LOGIN_MESSAGE)(
                    environ, start_response)
            return self._notice(message=FROZEN_LOGIN_MESSAGE)(
                environ, start_response)
        if path == "/" and request.method == "GET":
            return self._with_banner(
                environ, start_response,
                public_frozen_banner(self.state.settings.freeze_at))
        kind = FROZEN_ROUTES.get(path.strip("/").split("/")[0], "pass")
        if kind == "forbid":
            return Response("Este ranking está congelado.", status=403,
                            mimetype="text/plain",
                            headers=NO_STORE)(environ, start_response)
        if kind == "filter":
            environ[FREEZE_AT_ENVIRON] = self.state.settings.freeze_at
            start_response = _cache_control(start_response, "no-store")
        return self.app(environ, start_response)
```

   The `/events` route is `"pass"`: Task 5 filters its data.

- [ ] **Step 5: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/ -q`
Expected: all tests pass. If `test_every_route_is_classified` fails on
attribute names, inspect `build_ranking_app`'s return value and fix the
test's path to the `DispatcherMiddleware` and the `RoutingHandler`. The
set logic stays the same.

- [ ] **Step 6: Commit**

```bash
git add cmsranking/visibility.py cmsranking/RankingWebServer.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
git commit -m "feat(rws): serve a frozen group's scoreboard as of the freeze time"
```

---

### Task 5: Live streams across transitions

**Files:**
- Modify: `cmsranking/visibility.py`: `_guard_writes`, and `__call__` for
  `/events`.
- Modify: `cmsranking/static/DataStore.js`: the `reload` handler (~line
  903) and a `reinit` listener (~line 825).
- Modify: `cmscommon/eventsource.py:148`, so `reinit` carries a data line
  and browsers dispatch it.
- Test: `cmstestsuite/unit_tests/cmsranking/test_visibility.py`, adding
  `TestStreamsAcrossTransitions`, a subclass of `TestRealEventStream`
  (line 824), to reuse `open_stream` and `drain`.

**Interfaces:**
- Consumes: `VisibilityState.last_change` (Task 3), and the frozen state
  (Task 4).
- Produces:
  - a public `/events` stream never carries `score` events while frozen;
  - a public stream is cut once a scheduled time passes or the settings
    change after it opened;
  - a reconnection whose `Last-Event-ID` is older than the last change
    gets `event:reload`, and the page reloads itself.

- [ ] **Step 1: Write the failing tests** (a real gevent server, like the
existing class):

```python
class TestStreamsAcrossTransitions(TestRealEventStream):

    def test_frozen_stream_drops_score_events(self):
        self.put_contest("/olim")
        # Frozen since the epoch, until far in the future.
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 1,
            "unfreeze_at": None, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)
        stream = self.open_stream()
        for path, data in [
                ("tasks/", {"t": TestFrozenPublicView.TASK}),
                ("users/", {"u": {"f_name": "U", "l_name": "U",
                                  "team": None}}),
                ("submissions/", {"s": {"user": "u", "task": "t",
                                        "time": 100}}),
                ("subchanges/", {"c": {"submission": "s", "time": 100,
                                       "score": 40.0}})]:
            self.client.put("/olim/" + path, data=json.dumps(data),
                            content_type="application/json", headers=AUTH)
        data, _ = self.drain(stream)
        self.assertNotIn(b"event:score", data)
        self.assertIn(b"event:user", data)

    def test_reconnect_after_a_change_is_told_to_reload(self):
        self.put_contest("/olim")
        old_id = "%x" % int((time.time() - 60) * 1_000_000)
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": 1,
            "unfreeze_at": None, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)
        response = self.client.get("/olim/events",
                                   headers={"Last-Event-ID": old_id})
        self.assertIn(b"event:reload\ndata:\n\n", response.get_data())

    def test_open_stream_is_cut_when_the_freeze_starts(self):
        self.put_contest("/olim")
        start = time.time()
        self.client.put("/olim/visibility", data=json.dumps({
            "hide_at": None, "show_at": None, "freeze_at": int(start) + 2,
            "unfreeze_at": None, "staff_password": STAFF_HASH}),
            content_type="application/json", headers=AUTH)
        stream = self.open_stream()
        stream.settimeout(20)
        _, closed = self.drain(stream)   # the 15 s ping crosses freeze_at
        self.assertTrue(closed)
```

Write out the `...` in the first test before running it. Use the same
`client.put` calls as `TestFrozenPublicView.setUp`, sent after
`open_stream()`. The third test takes up to about 17 s, so mark it
`@unittest.skipUnless(os.environ.get("CMS_SLOW_TESTS"), "slow")` only if
the suite's time budget requires it. Otherwise keep it.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/test_visibility.py -q -k Transitions`
Expected: FAIL. Score events still get through, there is no reload, and
the stream is not cut.

- [ ] **Step 3: Implement.** In `_guard_writes`, add the stream's opening
time and the new checks:

```python
    def _guard_writes(self, request: Request, start_response):
        handler = getattr(start_response, "__self__", None)
        opened_at = time.time()

        def guarded_start_response(status, headers, exc_info=None):
            write = start_response(status, headers, exc_info)

            def guarded_write(data):
                if not self._is_staff(request):
                    now = time.time()
                    if self.state.settings.hidden(now) \
                            or self.state.last_change(now) > opened_at:
                        _close_connection(start_response)
                        raise ConnectionAbortedError(
                            "The ranking changed its visibility.")
                    if self.state.settings.frozen(now):
                        data = _drop_score_events(data)
                return write(data)

            return guarded_write

        if handler is not None:
            guarded_start_response.__self__ = handler
        return guarded_start_response
```

Add the helper:

```python
SCORE_EVENT = re.compile(rb"^event:score$", re.MULTILINE)


def _drop_score_events(data: bytes) -> bytes:
    """Remove the score events from a chunk of an event stream.

    data: one or more complete Server-Sent Events messages, each ended
        by a blank line, or a ping comment.

    return: the chunk without score events; a ping comment if nothing
        is left, since an empty write could end a chunked response.

    """
    messages = data.split(b"\n\n")
    kept = [m for m in messages if not SCORE_EVENT.search(m)]
    result = b"\n\n".join(kept)
    return result if result.strip() else b":\n"
```

In `__call__` (the Task 4 version), add this right after the staff
branch returns and before `if not hidden and not frozen:`. It applies to
public requests of a group that is not hidden:

```python
        if path == "/events" and request.method == "GET" \
                and not self._is_staff(request) and not hidden \
                and self._missed_a_change(request, now):
            return Response(b"event:reload\ndata:\n\n", status=200,
                            mimetype="text/event-stream",
                            headers=NO_STORE)(environ, start_response)
```

with:

```python
    def _missed_a_change(self, request: Request, now: float) -> bool:
        """Tell whether a reconnecting stream predates the last change.

        request: the /events request, with the ID of the last event the
            client got (the microseconds since the epoch, in hex).
        now: the current Unix time.

        return: True if that event is older than the last change of the
            public view, so replaying the events since then would be
            wrong.

        """
        last_id = request.headers.get("Last-Event-ID") \
            or request.args.get("last_event_id")
        if not last_id or not re.fullmatch(r"[0-9A-Fa-f]+", last_id):
            return False
        return int(last_id, 16) < self.state.last_change(now) * 1_000_000
```

In `cmscommon/eventsource.py`, change `queue.put(b"event:reinit\n\n")` to
`queue.put(b"event:reinit\ndata:\n\n")`. Without a data line, browsers
never dispatch the event.

In `cmsranking/static/DataStore.js`:
- after the `reload` listener line, add
  `self.es.addEventListener("reinit", self.es_reload_handler, false);`;
- inside `es_reload_handler`, replace `self.update_network_status(3);`
  with:

```javascript
            self.update_network_status(3);
            // The public view changed (freeze, unfreeze) or the events
            // missed are gone: the data on the page cannot be updated.
            window.location.reload();
```

Run `eslint cmsranking/static/DataStore.js` if eslint is configured for
that directory. Otherwise check the style by eye: 4 spaces and double
quotes.

- [ ] **Step 4: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/cmsranking/ -q`
Expected: all tests pass, including the existing `TestRealEventStream`.

- [ ] **Step 5: Commit**

```bash
git add cmsranking/visibility.py cmsranking/static/DataStore.js cmscommon/eventsource.py cmstestsuite/unit_tests/cmsranking/test_visibility.py
git commit -m "feat(rws): cut and reload public streams when the visibility changes"
```

---

### Task 6: Database columns and migration

**Files:**
- Modify: `cms/db/rankinggroup.py`, adding columns after `staff_password`
  and methods on `RankingGroup`.
- Modify: `cmscontrib/updaters/fork_multi_contest.py`, appending to
  `FORK_MULTI_CONTEST_SQL`.
- Test: `cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py`
  (asyncio group). Also run `cmstestsuite/unit_tests/schema_diff_test.py`.

**Interfaces:**
- Consumes: `window_is_open` (Task 1).
- Produces:
  - the columns `RankingGroup.hide_at`, `show_at`, `freeze_at`,
    `unfreeze_at`, each `datetime | None`, naive UTC;
  - `RankingGroup.is_hidden_at(now: datetime) -> bool`;
  - `RankingGroup.is_frozen_at(now: datetime) -> bool`;
  - `RankingGroup.hide_pending_at(now: datetime) -> bool`.

- [ ] **Step 1: Write the failing tests.** Read the existing tests in
`rankinggroup_visibility_test.py` and follow their DB setup. Add:

```python
    def test_windows(self):
        now = datetime(2026, 10, 10, 13, 0)
        group = RankingGroup(name="olim", description="O",
                             hide_at=now, show_at=now + timedelta(hours=3),
                             freeze_at=now - timedelta(hours=1))
        self.assertTrue(group.is_hidden_at(now))
        self.assertFalse(group.is_frozen_at(now))      # hidden wins
        self.assertTrue(group.is_frozen_at(now - timedelta(minutes=30)))
        self.assertTrue(group.hide_pending_at(now - timedelta(hours=1)))
        self.assertFalse(group.hide_pending_at(now + timedelta(hours=3)))

    def test_fork_sql_migrates_hidden_to_hide_at(self):
        # Rows written by MC-2 minimal: one hidden, one visible, no windows.
        run_sql("INSERT INTO ranking_groups (name, description, hidden) "
                "VALUES ('was_hidden', 'H', true), "
                "('was_visible', 'V', false);")
        # Idempotent: applying it twice must not fail or move hide_at.
        run_sql(FORK_MULTI_CONTEST_SQL)
        first = run_sql("SELECT hide_at FROM ranking_groups "
                        "WHERE name = 'was_hidden';")
        run_sql(FORK_MULTI_CONTEST_SQL)
        rows = dict(run_sql("SELECT name, hide_at IS NOT NULL "
                            "FROM ranking_groups "
                            "WHERE name IN ('was_hidden', 'was_visible');"))
        self.assertEqual(rows, {"was_hidden": True, "was_visible": False})
        self.assertEqual(run_sql("SELECT hide_at FROM ranking_groups "
                                 "WHERE name = 'was_hidden';"), first)
        late = run_sql("SELECT hide_at > (now() AT TIME ZONE 'UTC') "
                       "FROM ranking_groups WHERE name = 'was_hidden';")
        self.assertEqual(late, [(False,)])
```

Add `from datetime import datetime, timedelta` to the imports.

- [ ] **Step 2: Run the tests to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py -q`
(with `CMS_CONFIG` pointing at your private database)
Expected: FAIL. The columns and methods don't exist.

- [ ] **Step 3: Implement.** In `cms/db/rankinggroup.py`, add `DateTime`
to the types import, import `datetime`, and import `window_is_open` from
`cmscommon.ranking_groups`. Then:

```python
    # MC-2 phase 2: the public scoreboard is hidden during
    # [hide_at, show_at) and frozen during [freeze_at, unfreeze_at);
    # hidden wins. Naive UTC; None means the end is open.
    hide_at: datetime | None = Column(DateTime, nullable=True)
    show_at: datetime | None = Column(DateTime, nullable=True)
    freeze_at: datetime | None = Column(DateTime, nullable=True)
    unfreeze_at: datetime | None = Column(DateTime, nullable=True)

    def is_hidden_at(self, now: datetime) -> bool:
        """Tell whether the group is hidden at now (naive UTC)."""
        return window_is_open(self.hide_at, self.show_at, now)

    def is_frozen_at(self, now: datetime) -> bool:
        """Tell whether the group is frozen, and not hidden, at now."""
        return not self.is_hidden_at(now) and \
            window_is_open(self.freeze_at, self.unfreeze_at, now)

    def hide_pending_at(self, now: datetime) -> bool:
        """Tell whether the group is hidden or will be, as of now.

        This is what the hidden column keeps, so that a CMS rolled back
        to MC-2 minimal does not publish it.

        """
        return self.hide_at is not None and \
            (self.show_at is None or self.show_at > now)
```

Update the comment of the `hidden` column so it says that it is now
derived (AWS writes `hide_pending_at`), that it is kept for rollbacks to
MC-2 minimal, and that the new code doesn't read it.

Append to `FORK_MULTI_CONTEST_SQL`:

```sql
ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS hide_at timestamp without time zone;
ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS show_at timestamp without time zone;
ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS freeze_at timestamp without time zone;
ALTER TABLE public.ranking_groups
    ADD COLUMN IF NOT EXISTS unfreeze_at timestamp without time zone;
UPDATE public.ranking_groups
    SET hide_at = (now() AT TIME ZONE 'UTC')
    WHERE hidden AND hide_at IS NULL;
```

- [ ] **Step 4: Run the tests to see them pass**

Run the file above, then `schema_diff_test.py`, both in the asyncio group
process. Expected: both pass. If `schema_diff_test` reports a type
mismatch, align the SQL type with the model and not the other way round.
The model's `DateTime` maps to `timestamp without time zone`.

- [ ] **Step 5: Commit**

```bash
git add cms/db/rankinggroup.py cmscontrib/updaters/fork_multi_contest.py cmstestsuite/unit_tests/db/rankinggroup_visibility_test.py
git commit -m "feat(db): add the visibility windows of ranking groups"
```

---

### Task 7: ProxyService sends the windows

**Files:**
- Modify: `cms/service/ProxyService.py`, `_enqueue_visibility` (~line 936).
- Test: `cmstestsuite/unit_tests/service/proxyservice_groups_test.py`,
  updating `test_visibility_is_sent_before_group_data` (~line 703) and any
  other assertion on the visibility payload.

**Interfaces:**
- Consumes: the `RankingGroup` columns (Task 6).
- Produces: the new wire format (Global Constraints).

- [ ] **Step 1: Update the test first.** In
`test_visibility_is_sent_before_group_data`, expect:

```python
        self.assertEqual(self.put_payload(visibility),
                         {"hide_at": None, "show_at": None,
                          "freeze_at": None, "unfreeze_at": None,
                          "staff_password": None})
```

Add a test that sets `freeze_at = datetime(2026, 10, 10, 19, 0)` on the
fixture's group before `start()`, and expects `"freeze_at": 1791658800`
in the payload. Compute that expected value in the test with
`int(make_timestamp(datetime(2026, 10, 10, 19, 0)))` rather than
hard-coding it.

- [ ] **Step 2: Run to see it fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/service/proxyservice_groups_test.py -q`
Expected: FAIL, because the old payload is still sent.

- [ ] **Step 3: Implement**

```python
def _unix_time(value: datetime | None) -> int | None:
    """Convert a naive UTC datetime to Unix seconds, keeping None."""
    return None if value is None else int(make_timestamp(value))
```

(a module-level helper; import `make_timestamp` from `cmscommon.datetime`
if it isn't imported already). Then build the payload in
`_enqueue_visibility`:

```python
                {"hide_at": _unix_time(ranking_group.hide_at),
                 "show_at": _unix_time(ranking_group.show_at),
                 "freeze_at": _unix_time(ranking_group.freeze_at),
                 "unfreeze_at": _unix_time(ranking_group.unfreeze_at),
                 "staff_password": ranking_group.staff_password},
```

- [ ] **Step 4: Run the ProxyService test files to see them pass**

Run the 7 ProxyService/ScoringService test files in the asyncio group.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cms/service/ProxyService.py cmstestsuite/unit_tests/service/proxyservice_groups_test.py
git commit -m "feat(proxy): send the visibility windows of each ranking group"
```

---

### Task 8: AWS: windows, "now" buttons and state

**Files:**
- Modify: `cms/server/admin/handlers/rankinggroup.py`:
  - `read_ranking_group_visibility`;
  - the add and edit handlers;
  - a new helper, `visibility_view`.
- Modify: `cms/server/admin/templates/ranking_group.html` (the Hide row),
  `add_ranking_group.html`, `ranking_groups.html`.
- Test: `cmstestsuite/unit_tests/server/admin/rankinggroup_test.py`,
  replacing the `hidden`/`hidden_shown` tests with window tests, and
  updating `TestRankingGroupTemplates`.

**Interfaces:**
- Consumes: `check_window` (Task 1), and the `RankingGroup` columns and
  methods (Task 6).
- Produces:
  - `read_ranking_group_visibility(handler, attrs: dict, now: datetime)`,
    which raises `ValueError`;
  - `visibility_view(group: RankingGroup, now: datetime) -> dict`, with the
    keys `fields` (a list of dicts `name`, `label`, `utc`, `local`),
    `hidden_now`, `frozen_now`, `summary`, `next_change`.

- [ ] **Step 1: Write the failing tests** (reuse the file's
`visibility_handler(form)` fake):

```python
NOW = datetime(2026, 10, 10, 18, 0)


class TestReadWindows(unittest.TestCase):

    def read(self, form, attrs=None):
        attrs = dict() if attrs is None else attrs
        read_ranking_group_visibility(visibility_handler(form), attrs, NOW)
        return attrs

    def test_fields_are_parsed_as_utc(self):
        attrs = self.read({"freeze_at": "2026-10-10 19:00:00",
                           "unfreeze_at": ""})
        self.assertEqual(attrs["freeze_at"], datetime(2026, 10, 10, 19, 0))
        self.assertIsNone(attrs["unfreeze_at"])

    def test_stale_field_keeps_the_stored_value(self):
        stored = datetime(2026, 10, 10, 20, 0)
        attrs = self.read({"freeze_at": "2026-10-10 19:00:00",
                           "freeze_at_shown": "2026-10-10 19:00:00"},
                          {"freeze_at": stored})
        self.assertEqual(attrs["freeze_at"], stored)

    def test_changed_field_is_applied(self):
        attrs = self.read({"freeze_at": "2026-10-10 21:00:00",
                           "freeze_at_shown": "2026-10-10 19:00:00"},
                          {"freeze_at": datetime(2026, 10, 10, 20, 0)})
        self.assertEqual(attrs["freeze_at"], datetime(2026, 10, 10, 21, 0))

    def test_hide_now_keeps_a_future_show_and_clears_a_past_one(self):
        future = NOW + timedelta(hours=2)
        attrs = self.read({"visibility_action": "hide_now"},
                          {"show_at": future})
        self.assertEqual((attrs["hide_at"], attrs["show_at"]), (NOW, future))
        attrs = self.read({"visibility_action": "hide_now"},
                          {"show_at": NOW - timedelta(hours=1)})
        self.assertEqual((attrs["hide_at"], attrs["show_at"]), (NOW, None))

    def test_show_now_only_when_hidden(self):
        attrs = self.read({"visibility_action": "show_now"},
                          {"hide_at": NOW - timedelta(hours=1)})
        self.assertEqual(attrs["show_at"], NOW)
        with self.assertRaises(ValueError):
            self.read({"visibility_action": "show_now"}, {})

    def test_freeze_and_unfreeze_now(self):
        attrs = self.read({"visibility_action": "freeze_now"}, {})
        self.assertEqual(attrs["freeze_at"], NOW)
        attrs = self.read({"visibility_action": "unfreeze_now"},
                          {"freeze_at": NOW - timedelta(hours=1)})
        self.assertEqual(attrs["unfreeze_at"], NOW)

    def test_invalid_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "freeze"):
            self.read({"freeze_at": "2026-10-10 19:00:00",
                       "unfreeze_at": "2026-10-10 18:00:00"})

    def test_hidden_column_is_derived(self):
        attrs = self.read({"hide_at": "2026-10-10 20:00:00"})
        self.assertIs(attrs["hidden"], True)       # a pending hide
        attrs = self.read({"hide_at": "2026-10-10 10:00:00",
                           "show_at": "2026-10-10 12:00:00"})
        self.assertIs(attrs["hidden"], False)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read({"visibility_action": "explode"})
```

Keep the existing password tests. Change their calls to pass `NOW`. Delete
the `hidden_shown` tests, because the checkbox no longer exists.

In `TestRankingGroupTemplates`, add a test that renders `ranking_group.html`
with `view=visibility_view(group, NOW)` and asserts that:
- for each of the four fields there is an input and a `<field>_shown`
  input with the rendered UTC value, and `autocomplete="off"` on both;
- "Ocultar ahora" appears when the group isn't hidden, and "Mostrar ahora"
  when it is;
- the page has no `name="hidden"` checkbox any more;
- the password never leaks (the existing `assert_no_stored_password`).

- [ ] **Step 2: Run to see them fail**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/rankinggroup_test.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the reader**

```python
WINDOW_FIELDS = (
    ("hide_at", "Hide from (UTC)"),
    ("show_at", "Show again at (UTC)"),
    ("freeze_at", "Freeze at (UTC)"),
    ("unfreeze_at", "Unfreeze at (UTC)"),
)
FORM_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_utc(value: datetime | None) -> str:
    """Render a naive UTC datetime as the form shows it ("" for None)."""
    return "" if value is None else value.strftime(FORM_TIME_FORMAT)


def read_ranking_group_visibility(handler: BaseHandler, attrs: dict,
                                  now: datetime):
    """Read the visibility fields of the form into attrs.

    Each window time comes with <name>_shown, the value the page was
    rendered with; a time only counts when the organizer changed it, so
    that a stale page does not undo what somebody else scheduled. The
    add form has no _shown fields: its times always count. The
    visibility_action buttons set a time to now. The hidden column is
    derived from the windows, for rollbacks to MC-2 minimal.

    handler: the handler whose request carries the form.
    attrs: the group's current attributes, updated in place.
    now: the current time, naive UTC.

    raise (ValueError): on a malformed time, an invalid window, an
        action that does not apply, or an invalid staff password.

    """
    for name, _label in WINDOW_FIELDS:
        raw = handler.get_argument(name, None)
        if raw is None:
            continue
        raw = raw.strip()
        shown = handler.get_argument(name + "_shown", None)
        if shown is not None and raw == shown.strip() and name in attrs:
            continue
        attrs[name] = parse_datetime(raw) if raw else None

    action = handler.get_argument("visibility_action", None)
    group = RankingGroup(**{name: attrs.get(name)
                            for name, _label in WINDOW_FIELDS})
    if action == "hide_now":
        attrs["hide_at"] = now
        if attrs.get("show_at") is not None and attrs["show_at"] <= now:
            attrs["show_at"] = None
    elif action == "show_now":
        if not group.is_hidden_at(now):
            raise ValueError("The ranking is not hidden.")
        attrs["show_at"] = now
    elif action == "freeze_now":
        attrs["freeze_at"] = now
        if attrs.get("unfreeze_at") is not None \
                and attrs["unfreeze_at"] <= now:
            attrs["unfreeze_at"] = None
    elif action == "unfreeze_now":
        if not window_is_open(attrs.get("freeze_at"),
                              attrs.get("unfreeze_at"), now):
            raise ValueError("The ranking is not frozen.")
        attrs["unfreeze_at"] = now
    elif action is not None:
        raise ValueError("Unknown action %r." % (action,))

    check_window(attrs.get("hide_at"), attrs.get("show_at"), "hide")
    check_window(attrs.get("freeze_at"), attrs.get("unfreeze_at"),
                 "freeze")
    attrs["hidden"] = attrs.get("hide_at") is not None and (
        attrs.get("show_at") is None or attrs["show_at"] > now)

    # The staff password: unchanged from MC-2 minimal.
    new_password = handler.get_argument("staff_password", "", strip=False)
    remove = handler.get_argument(
        "remove_staff_password", None) is not None
    if new_password and remove:
        raise ValueError(
            "Set a new staff password or remove it, not both.")
    if remove:
        attrs["staff_password"] = None
    elif new_password:
        if len(new_password.encode("utf-8")) > MAX_STAFF_PASSWORD_BYTES:
            raise ValueError(
                "The staff password is too long (at most %d bytes)."
                % MAX_STAFF_PASSWORD_BYTES)
        attrs["staff_password"] = hash_password(new_password, "bcrypt")
```

Import `datetime` from `datetime`, `parse_datetime` from `.base`, and
`check_window`, `window_is_open` from `cmscommon.ranking_groups`. The
throwaway `RankingGroup(...)` is only used for `is_hidden_at`; it is never
added to a session. If constructing it triggers SQLAlchemy side effects,
replace it with `window_is_open(attrs.get("hide_at"), attrs.get("show_at"), now)`.

In both handlers' `_post_sync`, call
`read_ranking_group_visibility(self, attrs, make_datetime())`.
`make_datetime` is already imported.

- [ ] **Step 4: Implement the view helper and the templates**

```python
def visibility_view(group: RankingGroup, now: datetime) -> dict:
    """Prepare what the group page shows about its visibility.

    group: the ranking group.
    now: the current time, naive UTC.

    return: the fields (name, label, UTC value, local value), whether it
        is hidden or frozen now, a one-line summary and the next change.

    """
    def local(value: datetime | None) -> str:
        if value is None:
            return ""
        return value.replace(tzinfo=utc).astimezone(local_tz).strftime(
            "%Y-%m-%d %H:%M %Z")

    fields = [{"name": name, "label": label,
               "utc": format_utc(getattr(group, name)),
               "local": local(getattr(group, name))}
              for name, label in WINDOW_FIELDS]
    hidden = group.is_hidden_at(now)
    frozen = group.is_frozen_at(now)
    summary = "oculto" if hidden else "congelado" if frozen else "visible"
    upcoming = sorted((getattr(group, name), label)
                      for name, label in WINDOW_FIELDS
                      if getattr(group, name) is not None
                      and getattr(group, name) > now)
    next_change = "" if not upcoming else "%s: %s" % (
        upcoming[0][1], local(upcoming[0][0]))
    return {"fields": fields, "hidden_now": hidden, "frozen_now": frozen,
            "summary": summary, "next_change": next_change}
```

Import `utc` and `local_tz` from `cmscommon.datetime`. In
`RankingGroupHandler._get_sync`, add
`self.r_params["view"] = visibility_view(group, make_datetime())`. In the
list handler, add a `views` dict from group id to `visibility_view(...)`.

In `ranking_group.html`, replace the whole "Hide ranking from the public"
`<tr>` with:

```html
      <tr>
        <td>Now</td>
        <td><strong>{{ view.summary }}</strong>{% if view.next_change %} &middot; next: {{ view.next_change }}{% endif %}</td>
      </tr>
      {% for f in view.fields %}
      <tr>
        <td>{{ f.label }}</td>
        <td>
          <input type="text" autocomplete="off" name="{{ f.name }}" value="{{ f.utc }}" placeholder="YYYY-MM-DD HH:MM:SS"/>
          <!-- What the field was rendered with: only a change to it counts, so a stale page does not undo somebody else's schedule. -->
          <input type="hidden" autocomplete="off" name="{{ f.name }}_shown" value="{{ f.utc }}"/>
          {% if f.local %}= {{ f.local }}{% endif %}
        </td>
      </tr>
      {% endfor %}
      <tr>
        <td></td>
        <td>
          {% if view.hidden_now %}
          <button type="submit" name="visibility_action" value="show_now">Mostrar ahora</button>
          {% else %}
          <button type="submit" name="visibility_action" value="hide_now">Ocultar ahora</button>
          {% endif %}
          {% if view.frozen_now %}
          <button type="submit" name="visibility_action" value="unfreeze_now">Descongelar ahora</button>
          {% else %}
          <button type="submit" name="visibility_action" value="freeze_now">Congelar ahora</button>
          {% endif %}
        </td>
      </tr>
```

In `add_ranking_group.html`, replace the Hide row with the four inputs
only: no `_shown` fields, no buttons, and empty values. In
`ranking_groups.html`, replace the "hidden" marker with
`{{ views[g.id].summary }}` plus `views[g.id].next_change`. Keep the
"password set" marker and the warning for a hidden group without a
password, now based on `views[g.id].hidden_now`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `timeout --foreground --signal=ABRT 900 .venv/bin/pytest cmstestsuite/unit_tests/server/admin/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add cms/server/admin/handlers/rankinggroup.py cms/server/admin/templates/ranking_group.html cms/server/admin/templates/add_ranking_group.html cms/server/admin/templates/ranking_groups.html cmstestsuite/unit_tests/server/admin/rankinggroup_test.py
git commit -m "feat(aws): schedule hiding and freezing of ranking groups"
```

---

### Task 9: Functional test: a frozen group

**Files:**
- Modify: `cmstestsuite/testrunner.py`, `check_ranking_visibility`, added
  by commit `a505b4fb`; read it first.
- Modify: `cmstestsuite/functionaltestframework.py`, the
  `edit_ranking_group` helper from the same commit, which must send the
  new fields.

**Interfaces:**
- Consumes: the AWS form of Task 8 (field names, `visibility_action`), and
  the RWS behaviour of Tasks 4 and 5.

- [ ] **Step 1: Adapt the hide/unhide steps to the new form.**
  - Hiding sends `visibility_action=hide_now`.
  - Unhiding sends `visibility_action=show_now`.
  - Edits send each `<field>_shown` exactly as the page renders it. Fetch
    the page and parse the hidden inputs with a regex, the way the harness
    already reads ids.
- [ ] **Step 2: Add a freeze phase** after the unhide:
  1. `visibility_action=freeze_now`.
  2. Poll until the public `/<g>/` contains "Ranking congelado".
  3. Assert that `/<g>/submissions/` gives 403, that `/<g>/scores` gives
     200 with `no-store`, and that `/<g>/staff-login` gives 200 with a
     password field.
  4. Log in as staff: 303 and a cookie. The staff `/` contains
     "ranking congelado para el público".
  5. `visibility_action=unfreeze_now`, then poll until the banner is gone.
- [ ] **Step 3: Validate with 3 full local CI runs** on rootful Docker,
  the same procedure as the commit that added the check: a `git archive`
  snapshot, the `main.yml` test job, and teardown after each run. All 3
  must pass.
- [ ] **Step 4: Commit**

```bash
git add cmstestsuite/testrunner.py cmstestsuite/functionaltestframework.py
git commit -m "test(functional): freeze and unfreeze a ranking group"
```

---

### Task 10: Operator documentation

**Files:**
- Modify: `docs/multi-contest.md`, the "Hiding a ranking (staff view)"
  section and "Before an exam day" step 1.

- [ ] **Step 1:** Rewrite the hiding section for the windows:
  - The four fields are in UTC, with the local equivalent shown after
    saving.
  - The "… now" buttons, including that "Ocultar ahora" keeps a future
    show time and clears a past one.
  - Hidden wins over frozen.
  - What the public and the staff see while frozen: the banner, "Acceso
    staff", no live updates, late evaluations of submissions made before
    the freeze appear on reload, and the page reloads by itself at each
    transition.
  - The time zone warning: enter UTC.
- [ ] **Step 2:** Update "Before an exam day" step 1 to "set the hide or
  freeze windows at creation".
- [ ] **Step 3:** Add a "Checking a schedule" paragraph. After saving,
  compare the local equivalents with the contest times, and open the public
  URL in a private window.
- [ ] **Step 4: Commit**

```bash
git add docs/multi-contest.md
git commit -m "docs(mc2): explain the scheduled hide and freeze windows"
```

---

### Task 11: End-to-end verification in Chromium (no code)

Run the same kind of check as the MC-2 minimal E2E, on a local
multi-contest stack built from the branch tip. Use rootful Docker; the
procedure is in `.superpowers/scratch/e2e-stack/stack.sh` and
`.superpowers/scratch/e2e-browser/`. Record the results in the ledger:

1. **Freeze.** Schedule a freeze 2 minutes ahead and an unfreeze 4 minutes
   ahead.
   - Submit as contestant A before the freeze, and as contestant B after
     it.
   - The public page shows the freeze banner, and B's score never appears.
   - A staff page shows B live.
   - At the unfreeze the public page reloads by itself and shows B.
2. **RWS restart.** Restart RWS during the freeze (`./up.sh` → 3). The
   snapshot is the same afterwards.
3. **Hide window.** Schedule a hide window 1 minute ahead, lasting 1
   minute. The public URL switches to the notice and back by itself.
4. **AWS.**
   - The local equivalents are shown.
   - The "… now" buttons behave as in Task 8.
   - A stale page doesn't undo another organizer's schedule.
5. **Logs.** No JS errors, no 5xx, and no WARNING or ERROR in the
   ranking, proxy or AWS logs.
