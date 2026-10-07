# Arrival-Time Phase Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A submission that reached the deployment before the contest stop
is accepted even when ContestWebServer handles it after the stop, and a
refused submission always tells the contestant why (#5).

**Architecture:** A pure helper computes a request's arrival time from the
handler time, Tornado's elapsed time and, when CWS is configured with a
header name, a time that a trusted proxy wrote. The helper clamps the
result to 60 s before the handler time. `CommonRequestHandler` uses that
time as `self.timestamp`, while the countdown display keeps the real time.
The phase decorator and `SubmitHandler` add an error notification when they
refuse a web submit. The configuration reaches Docker through a new env
var, and the load harness can simulate the proxy header for a validation
run.

**Tech Stack:** Python 3.12, Tornado 6.5, SQLAlchemy 2, pytest/unittest,
Babel catalogs, Sphinx + sphinx-intl for the manual, bash, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-07-cws-arrival-time-design.md`

## Global Constraints

- **Where to work.** Work only in the worktree
  `/var/home/areslolxd/Documentos/cms/.worktrees/cws-arrival` (branch
  `feat/cws-arrival-time`). Commit there. Push only to `loadtest/<topic>`
  branches (Task 7), never to beta or main.
- **Python style.** Code and comments in English, PEP 8, PEP 484 hints,
  and the project docstring format (imperative first line, then
  `arg (type): ...`, `return (type): ...` and `raise (Exc): ...` lines; see
  `CONTRIBUTING.md`).
- **pyflakes must be clean:**
  `uvx -q pyflakes cms cmscommon cmscontrib cmsranking cmstaskenv cmstestsuite docker`
  must show no new warning on the lines you touched.
- **License header for new files:** copy the header from
  `cmstestsuite/unit_tests/asyncwait.py` lines 1-17, including the line
  `# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>`.
- **Commits.** Follow Conventional Commits. End each message with a
  `Refs #5` line, a blank line, then
  `Co-Authored-By: <your model name> <noreply@anthropic.com>`.
- **Python environment.** Create the venv if it is missing:
  `uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -c constraints.txt -e ".[devel]"`.
  Run tests with `.venv/bin/python -m pytest -p no:cacheprovider <files>`.
- **Tests that need the DB.** Set
  `CMS_CONFIG=/var/home/areslolxd/Documentos/cms/.worktrees/cws-arrival/.superpowers/sdd/2026-10-07-cws-arrival-time/cms-test.toml`
  (Postgres container `cms-test-pg` on localhost:55432; start it with
  `podman start cms-test-pg` if it is down). Run one pytest process at a
  time, because the DB tests share one database.
- **The clamp.** `MAX_SKEW` is exactly 60 seconds.
- **Header formats.**
  - Seconds or milliseconds since the Unix epoch, with an optional `t=`
    prefix.
  - A number at or above 10**11 means milliseconds.
  - Anything else is ignored with a debug log, never an error.
- **The new config key** is `contest_web_server.request_time_header`
  (string, default `""` = off). Its Docker env var is
  `CMS_CWS_REQUEST_TIME_HEADER`.
- **New user-facing strings** (CWS catalog `cms/locale/cms.pot` and
  `cms/locale/es/LC_MESSAGES/cms.po` only, edited by hand like commit
  8a820ae5):
  - `"Submission not accepted"` → `"Envío no aceptado"`
  - `"Test not accepted"` → `"Prueba no aceptada"`
- **Reused msgids** (already translated): `"The contest has already ended."`
  and `"The contest hasn't started yet."`.

## Review Focus

- **A header sent while CWS has no header configured** must be ignored (a
  contestant trying to backdate a submission). Pinned in Task 2
  (`test_header_ignored_when_not_configured`).
- **A header time far in the past** (forged, or a skewed proxy clock) can
  move the time back at most 60 s. Pinned in Task 1 (clamp tests) and
  Task 2 (`test_header_clamped_to_max_skew`).
- **A header time in the future** must never make a request later than the
  handler time. Pinned in Task 1 (`test_future_header_ignored`).
- **The countdown and clock shown to the contestant** must not run behind
  by the queueing delay: `render_params()["now"]` uses the real handler
  time. Pinned in Task 2 (`test_now_is_handler_time`).
- **AWS and test doubles whose service has no `request_time_header`**
  (a MagicMock service, an AWS service) must not crash or read a mock as a
  header name. Pinned in Task 2 (`test_service_without_header_attribute`).

---

### Task 1: The arrival-time helper

**Files:**
- Create: `cms/server/request_time.py`
- Test: `cmstestsuite/unit_tests/server/request_time_test.py`

**Interfaces:**
- Produces:
  - `MAX_SKEW: timedelta` (60 s).
  - `parse_request_time_header(value: str | None) -> datetime | None`:
    returns a naive UTC datetime, the same kind `cmscommon.datetime.make_datetime()`
    returns, or None.
  - `request_arrival_time(handler_time: datetime, elapsed: float | None, header_time: datetime | None, max_skew: timedelta = MAX_SKEW) -> tuple[datetime, str]`:
    returns the effective time and its source, one of `"handler"`,
    `"tornado"` or `"header"`.

- [ ] **Step 1: Check what kind of datetime CMS uses**

Run: `grep -n "def make_datetime" -A12 cmscommon/datetime.py`.
Expected: it returns a naive datetime in UTC. If it returns an aware
datetime instead, make `parse_request_time_header` return the same kind,
and adapt the tests below. Say which one in your report.

- [ ] **Step 2: Write the failing tests**

`cmstestsuite/unit_tests/server/request_time_test.py` (license header
first):

```python
"""Tests for the request arrival time helper."""

import unittest
from datetime import datetime, timedelta

from cms.server.request_time import (MAX_SKEW, parse_request_time_header,
                                     request_arrival_time)

T = datetime(2026, 10, 10, 20, 0, 0)  # the handler time in these tests
EPOCH = datetime(1970, 1, 1)


def unix(dt: datetime) -> float:
    return (dt - EPOCH).total_seconds()


class ParseHeaderTest(unittest.TestCase):

    def test_seconds_with_fraction(self):
        self.assertEqual(parse_request_time_header("%.3f" % unix(T)), T)

    def test_milliseconds_integer(self):
        self.assertEqual(
            parse_request_time_header("%d" % (unix(T) * 1000)), T)

    def test_t_prefix(self):
        self.assertEqual(
            parse_request_time_header("t=%d" % (unix(T) * 1000)), T)
        self.assertEqual(
            parse_request_time_header("t=%.3f" % unix(T)), T)

    def test_garbage_is_ignored(self):
        for value in (None, "", "   ", "abc", "t=", "-5", "1e12", "t=12,5",
                      "99999999999999999999999"):
            with self.subTest(value=value):
                self.assertIsNone(parse_request_time_header(value))


class ArrivalTimeTest(unittest.TestCase):

    def test_handler_time_alone(self):
        self.assertEqual(request_arrival_time(T, None, None), (T, "handler"))

    def test_tornado_elapsed_moves_it_back(self):
        self.assertEqual(request_arrival_time(T, 2.5, None),
                         (T - timedelta(seconds=2.5), "tornado"))

    def test_header_wins_when_earliest(self):
        header = T - timedelta(seconds=9)
        self.assertEqual(request_arrival_time(T, 2.5, header),
                         (header, "header"))

    def test_tornado_wins_over_later_header(self):
        header = T - timedelta(seconds=1)
        self.assertEqual(request_arrival_time(T, 2.5, header),
                         (T - timedelta(seconds=2.5), "tornado"))

    def test_future_header_ignored(self):
        self.assertEqual(
            request_arrival_time(T, None, T + timedelta(seconds=30)),
            (T, "handler"))

    def test_clamped_to_max_skew(self):
        self.assertEqual(MAX_SKEW, timedelta(seconds=60))
        header = T - timedelta(hours=1)
        self.assertEqual(request_arrival_time(T, None, header),
                         (T - MAX_SKEW, "header"))

    def test_negative_or_zero_elapsed_ignored(self):
        self.assertEqual(request_arrival_time(T, 0.0, None), (T, "handler"))
        self.assertEqual(request_arrival_time(T, -1.0, None), (T, "handler"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run them and check that they fail**

Run: `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/server/request_time_test.py -q`
Expected: ModuleNotFoundError for `cms.server.request_time`.

- [ ] **Step 4: Implement**

`cms/server/request_time.py` (license header first):

```python
"""When a request reached the deployment, as early as can be trusted.

Under load a request can wait seconds before ContestWebServer builds its
handler, so the handler's own time can turn an in-time submission into a
late one. The arrival time is the earliest of the handler time, the
handler time minus Tornado's elapsed time for the request, and a time a
trusted front proxy wrote in a header. It is never earlier than
MAX_SKEW before the handler time.

"""

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# How far before the handler time the arrival time can go. Bounds the
# effect of a skewed proxy clock or a forged header that slipped through.
MAX_SKEW = timedelta(seconds=60)

# A header value at or above this is in milliseconds, below it in seconds.
_MILLISECONDS_THRESHOLD = 10 ** 11

_HEADER_VALUE = re.compile(r"^\s*(?:t=)?(\d+(?:\.\d+)?)\s*$")


def parse_request_time_header(value: str | None) -> datetime | None:
    """Parse a proxy's request time header.

    value (str|None): the header value: Unix time in seconds (with or
        without a fraction) or milliseconds, optionally prefixed by "t=".

    return (datetime|None): the time as a naive UTC datetime, or None if
        the value is missing or not understood.

    """
    if not value:
        return None
    match = _HEADER_VALUE.match(value)
    if match is None:
        logger.debug("Ignoring request time header %r.", value)
        return None
    number = float(match.group(1))
    if number >= _MILLISECONDS_THRESHOLD:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc).replace(
            tzinfo=None)
    except (OverflowError, OSError, ValueError):
        logger.debug("Ignoring request time header %r.", value)
        return None


def request_arrival_time(
    handler_time: datetime,
    elapsed: float | None,
    header_time: datetime | None,
    max_skew: timedelta = MAX_SKEW,
) -> tuple[datetime, str]:
    """Return when a request arrived, as early as can be trusted.

    handler_time (datetime): when the handler was built.
    elapsed (float|None): seconds Tornado reports since it read the
        request headers, or None.
    header_time (datetime|None): the time a trusted proxy wrote, or None.
    max_skew (timedelta): how far before handler_time the result can go.

    return ((datetime, str)): the arrival time and its source, one of
        "handler", "tornado" and "header".

    """
    candidates = [(handler_time, "handler")]
    if elapsed is not None and elapsed > 0:
        candidates.append(
            (handler_time - timedelta(seconds=elapsed), "tornado"))
    if header_time is not None and header_time <= handler_time:
        candidates.append((header_time, "header"))
    earliest, source = min(candidates, key=lambda candidate: candidate[0])
    return max(earliest, handler_time - max_skew), source
```

If Step 1 showed that `make_datetime()` returns an aware datetime, drop the
`.replace(tzinfo=None)`.

- [ ] **Step 5: Run the tests**

Expected: 11 passed. pyflakes is clean on both files.

- [ ] **Step 6: Commit**

Message: `feat(server): compute a request's arrival time` (+ `Refs #5`).

---

### Task 2: CWS uses the arrival time

**Files:**
- Modify:
  - `cms/conf.py` (`CWSConfig`: add `request_time_header: str = ""` after
    `num_proxies_used`)
  - `cms/server/contest/server.py` (`ContestWebServer.__init__`)
  - `cms/server/util.py:266-277` (`CommonRequestHandler.__init__`)
  - `cms/server/contest/handlers/base.py:137` (`ret["now"]`)
  - `config/cms.sample.toml` (`[contest_web_server]`)
- Test:
  - `cmstestsuite/unit_tests/server/contest/arrival_time_test.py` (new,
    uses the `CwsTestBase` pattern from
    `cmstestsuite/unit_tests/server/contest/xsrf_error_page_test.py:46-136`)
  - `cmstestsuite/unit_tests/server/util_test.py` (only if its MagicMock
    services need `request_time_header=""`)

**Interfaces:**
- Consumes: `parse_request_time_header`, `request_arrival_time`
  (Task 1).
- Produces:
  - `CommonRequestHandler.handler_time: datetime` (the old value).
  - `CommonRequestHandler.timestamp: datetime` (now the arrival time).
  - `ContestWebServer.request_time_header: str`.

- [ ] **Step 1: Write the failing tests**

`arrival_time_test.py` subclasses `CwsTestBase`. Import it from
`xsrf_error_page_test`, or copy the minimum if importing it would collect
its tests twice; check how other tests reuse it.

- In `setUp`, after `super().setUp()`, give the contest:
  - a task with a submission format and an active dataset (see
    `DatabaseMixin.add_task` / `add_dataset` in
    `cmstestsuite/unit_tests/databasemixin.py`, and
    `submission_format=["sol.%l"]`);
  - a language: `self.contest.languages = ["C++17 / g++"]`;
  - a main group whose `stop` is **30 s before now** and whose `start` is
    one hour before.

  Use `make_datetime()` for "now".

Define in the test module: `EPOCH = datetime(1970, 1, 1)` and
`unix(dt) -> float` as in `request_time_test.py`; in `setUp`, `self.now =
make_datetime()` and `self.stop = self.now - timedelta(seconds=30)` (used
for the group); and an `async def submit(self, headers)` helper that logs
in, fetches the XSRF token, POSTs the multipart form with the extra
`headers`, and returns the response (`follow_redirects=False`).

Tests (each logs in with the existing login helper pattern of
`session_renewal_test.py:150-160`, then POSTs `/tasks/<task>/submit` with
`_xsrf`, `language=C++17 / g++` and a small `sol.cpp` file, as multipart):

```python
    async def test_header_moves_submission_before_the_stop(self):
        self.cws.request_time_header = "X-Request-Start"
        arrival = self.stop - timedelta(seconds=5)
        resp = await self.submit(headers={
            "X-Request-Start": "t=%d" % (unix(arrival) * 1000)})
        self.assertIn("submission_id=", resp.headers["Location"])
        stored = self.session.query(Submission).one()
        self.assertAlmostEqual(
            (stored.timestamp - arrival).total_seconds(), 0, delta=0.01)

    async def test_header_ignored_when_not_configured(self):
        self.cws.request_time_header = ""
        arrival = self.stop - timedelta(seconds=5)
        resp = await self.submit(headers={
            "X-Request-Start": "t=%d" % (unix(arrival) * 1000)})
        self.assertNotIn("submission_id=", resp.headers["Location"])
        self.assertEqual(self.session.query(Submission).count(), 0)

    async def test_header_clamped_to_max_skew(self):
        # The stop was 30 s ago: a header 10 min old is clamped to
        # handler time - 60 s, which is still before the stop, so the
        # submission is accepted with a timestamp about 60 s ago.
        self.cws.request_time_header = "X-Request-Start"
        resp = await self.submit(headers={
            "X-Request-Start": "%.3f" % unix(self.now - timedelta(minutes=10))})
        self.assertIn("submission_id=", resp.headers["Location"])
        stored = self.session.query(Submission).one()
        self.assertLess(
            abs((self.now - stored.timestamp).total_seconds() - 60), 5)
```

Also add two handler-level tests. Build `CommonRequestHandler` subclasses
the way `util_test.py`'s `AuthExtensionPointTest` does, so the real
`__init__` runs:

```python
    def test_now_is_handler_time(self):
        # A handler with a 3 s Tornado elapsed time: timestamp is 3 s
        # earlier than handler_time, and BaseHandler.render_params()["now"]
        # equals handler_time.

    def test_service_without_header_attribute(self):
        # A service object with no request_time_header attribute (or a
        # MagicMock one): __init__ does not fail and only Tornado's time
        # is used.
```

Write those two bodies out completely, with asserts on `handler.timestamp`,
`handler.handler_time` and `render_params()["now"]`. Mock
`request.request_time()` to return 3.0. Read `util_test.py:361-530` first:
it shows how its tests build a request and a handler without a server.

- [ ] **Step 2: Run them and check that they fail**

Run with `CMS_CONFIG` set (see Global Constraints).
Expected: the header tests fail (the submission is refused, or
`AttributeError` on `request_time_header`), and `test_now_is_handler_time`
fails on `handler_time`.

- [ ] **Step 3: Implement**

- `cms/conf.py`, in `CWSConfig`: `request_time_header: str = ""`.
- `cms/server/contest/server.py`, at the end of `ContestWebServer.__init__`:

  ```python
          # Name of a header in which a trusted front proxy writes when it
          # received each request (see cms/server/request_time.py). Empty:
          # off.
          self.request_time_header: str = \
              config.contest_web_server.request_time_header
  ```

- `cms/server/util.py`, in `CommonRequestHandler.__init__`, replace
  `self.timestamp = make_datetime()` with:

  ```python
          self.handler_time = make_datetime()
          header_name = getattr(self.service, "request_time_header", "")
          header_time = None
          if isinstance(header_name, str) and header_name:
              header_time = parse_request_time_header(
                  self.request.headers.get(header_name))
          self.timestamp, source = request_arrival_time(
              self.handler_time, self.request.request_time(), header_time)
          delay = (self.handler_time - self.timestamp).total_seconds()
          if delay > 1:
              logger.info("Request %s %s arrived %.1f s before its handler "
                          "ran (source: %s).", self.request.method,
                          self.request.path, delay, source)
  ```

  Import `parse_request_time_header` and `request_arrival_time` from
  `cms.server.request_time`. Check that `cms/server/util.py` has a module
  `logger`; if it does not, add one in the style of the file.
- `cms/server/contest/handlers/base.py:137`: `ret["now"] = self.handler_time`,
  with a one-line comment: the clock and countdown shown to the contestant
  use the real time, not the arrival time.
- `config/cms.sample.toml`, in `[contest_web_server]` after
  `num_proxies_used`, a commented key in the file's style:

  ```toml
  # Name of a header in which a front proxy writes when it received each
  # request, so that a submission that reached the proxy before the contest
  # stop is accepted even if CWS handles it later (e.g. Caddy:
  # header_up X-Request-Start "t={time.now.unix_ms}"). Set it only if every
  # request reaches CWS through that proxy, which must overwrite the
  # header, and if the proxy and CWS share a clock. Empty: off.
  #request_time_header = "X-Request-Start"
  ```

- [ ] **Step 4: Run the new tests plus the existing CWS and util tests**

Run, one process at a time:
- `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/server -q`
- `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/server/contest -q`

Expected: everything passes.
- `session_renewal_test.py` patches `cms.server.util.make_datetime`. It
  must still pass, because the Tornado candidate is computed from
  `handler_time` minus the elapsed time.
- If a `util_test.py` MagicMock service breaks, set
  `request_time_header=""` on it explicitly, as its comment at :379-383
  does for `num_proxies_used`.

- [ ] **Step 5: Commit**

Message: `feat(contest): decide the phase by the request's arrival time` (+ `Refs #5`).

---

### Task 3: Tell the contestant when a submission is refused

**Files:**
- Modify:
  - `cms/server/contest/phase_management.py` (`actual_phase_required`,
    :205-244; new `phase_refusal_text`)
  - `cms/server/contest/handlers/tasksubmission.py` (`SubmitHandler`,
    :73-120)
  - `cms/server/contest/handlers/taskusertest.py` (`UserTestHandler`,
    :121-162)
  - `cms/locale/cms.pot` and `cms/locale/es/LC_MESSAGES/cms.po`
- Test: `cmstestsuite/unit_tests/server/contest/phase_refusal_test.py`
  (new)

**Interfaces:**
- Produces:
  - `phase_refusal_text(actual_phase: int) -> str`: the msgid
    `"The contest hasn't started yet."` when `actual_phase < 0`, otherwise
    `"The contest has already ended."`.
  - `actual_phase_required(*actual_phases: int, refusal_subject: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

Use the `__new__` pattern of
`cmstestsuite/unit_tests/server/contest/handlers_schedule_rpc_test.py:60-95`:

```python
"""Tests for the notifications of refused submissions."""

import unittest
from unittest.mock import MagicMock

from cms.server.contest.handlers.api import ApiSubmitHandler
from cms.server.contest.handlers.tasksubmission import SubmitHandler
from cms.server.contest.handlers.taskusertest import UserTestHandler
from cms.server.contest.phase_management import phase_refusal_text


def make_handler(cls, actual_phase, api=False, allow_unofficial=False):
    handler = cls.__new__(cls)
    handler._current_user = MagicMock(unrestricted=False)
    handler.impersonated_by_admin = False
    handler.api_request = api
    handler.is_multi_contest = lambda: False
    handler.r_params = {"actual_phase": actual_phase,
                        "testing_enabled": True}
    handler.request = MagicMock(method="POST", arguments={}, files={})
    handler.contest = MagicMock(
        allow_unofficial_submission_before_analysis_mode=allow_unofficial)
    handler.contest_url = MagicMock(return_value="/")
    handler.redirect = MagicMock()
    handler.notify_error = MagicMock()
    handler.json = MagicMock()
    return handler


class PhaseRefusalTextTest(unittest.TestCase):

    def test_texts(self):
        for phase in (-2, -1):
            self.assertEqual(phase_refusal_text(phase),
                             "The contest hasn't started yet.")
        for phase in (1, 2, 3, 4):
            self.assertEqual(phase_refusal_text(phase),
                             "The contest has already ended.")


class SubmitRefusalTest(unittest.TestCase):

    def test_after_the_contest(self):
        handler = make_handler(SubmitHandler, 4)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest has already ended.")
        handler.redirect.assert_called_once_with("/")

    def test_before_the_contest(self):
        handler = make_handler(SubmitHandler, -2)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest hasn't started yet.")

    def test_in_phase_refusal_without_unofficial_submissions(self):
        handler = make_handler(SubmitHandler, 2, allow_unofficial=False)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Submission not accepted", "The contest has already ended.")
        handler.redirect.assert_called_once_with("/")

    def test_api_still_gets_json(self):
        handler = make_handler(ApiSubmitHandler, 4, api=True)
        handler.post("task")
        handler.json.assert_called_once_with(
            {"error": "The contest is not open"}, 403)
        handler.notify_error.assert_not_called()


class UserTestRefusalTest(unittest.TestCase):

    def test_after_the_contest(self):
        handler = make_handler(UserTestHandler, 4)
        handler.post("task")
        handler.notify_error.assert_called_once_with(
            "Test not accepted", "The contest has already ended.")
```

If `tornado.web.authenticated` or `multi_contest` needs more attributes on
the `__new__`-built handler, set them as `handlers_schedule_rpc_test.py`
does. Do not weaken the asserts.

- [ ] **Step 2: Run them and check that they fail**

Expected: ImportError for `phase_refusal_text`, then
`notify_error` not called.

- [ ] **Step 3: Implement**

In `phase_management.py`:

```python
# Dummy function to mark translatable strings.
def N_(msgid: str) -> str:
    return msgid


def phase_refusal_text(actual_phase: int) -> str:
    """Return why a request is refused in the given actual phase.

    actual_phase (int): a phase from compute_actual_phase, other than 0.

    return (str): an untranslated message id.

    """
    if actual_phase < 0:
        return N_("The contest hasn't started yet.")
    return N_("The contest has already ended.")
```

Change the decorator's signature to
`def actual_phase_required(*actual_phases: int, refusal_subject: str | None = None)`,
document the new argument in its docstring, and replace the non-API branch
(the `# TODO maybe return some error code?` line goes away) with:

```python
                else:
                    if refusal_subject is not None:
                        self.notify_error(
                            refusal_subject,
                            phase_refusal_text(self.r_params["actual_phase"]))
                    self.redirect(self.contest_url())
```

In `SubmitHandler`:
- The decorator becomes
  `@actual_phase_required(0, 1, 2, 3, refusal_subject=N_("Submission not accepted"))`.
- In the in-phase refusal at :83-86, before `self.redirect(...)`, add
  `self.notify_error(N_("Submission not accepted"), phase_refusal_text(self.r_params["actual_phase"]))`.
- Import `phase_refusal_text` next to `actual_phase_required`.

In `UserTestHandler`, the decorator becomes
`@actual_phase_required(0, refusal_subject=N_("Test not accepted"))`.

Catalogs: add the two new msgids to `cms/locale/cms.pot` next to
`"Submission received"` (around :264), and to
`cms/locale/es/LC_MESSAGES/cms.po` with the Spanish strings from the Global
Constraints, in the same format as their neighbours. Confirm that the two
reused msgids are already in both files (`grep -n "already ended\|hasn't started" cms/locale/cms.pot cms/locale/es/LC_MESSAGES/cms.po`).
Then:
- Compile the Spanish catalog:
  `.venv/bin/python -m babel.messages.frontend compile -d cms/locale -D cms -l es`,
  or `./setup.py compile_catalog` per `docs/Localization.rst`.
- Run `msgfmt -c --check-format -o /dev/null cms/locale/es/LC_MESSAGES/cms.po`.
- Do not commit the compiled `.mo` unless the repo tracks it
  (`git ls-files 'cms/locale/*.mo'`).

- [ ] **Step 4: Run the tests**

Run: `phase_refusal_test.py`, `handlers_schedule_rpc_test.py` and
`cmstestsuite/unit_tests/server/contest/phase_management_test.py`.
Expected: all pass. pyflakes is clean.

- [ ] **Step 5: Commit**

Message: `feat(contest): tell contestants when a submission is refused by the contest phase` (+ `Refs #5`).

---

### Task 4: Docker configuration

**Files:**
- Modify: `docker/generate_config.py` (near the `CMS_CWS_COOKIE_DURATION`
  block at :85-96; the `[contest_web_server]` template at :151-155)
- Modify: `.env.example` (the "REVERSE PROXY (optional)" section, :177-197)
- Test: `docker/test_generate_config.py` (next to the cookie-duration tests
  at :176-203)

**Interfaces:**
- Produces: env var `CMS_CWS_REQUEST_TIME_HEADER` → `[contest_web_server] request_time_header`.

- [ ] **Step 1: Write the failing tests**

```python
def test_request_time_header_default_is_off(monkeypatch):
    _set(monkeypatch, {})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["request_time_header"] == ""


def test_request_time_header_custom(monkeypatch):
    _set(monkeypatch, {"CMS_CWS_REQUEST_TIME_HEADER": "X-Request-Start"})
    cws = tomllib.loads(gc.generate_cms_toml())["contest_web_server"]
    assert cws["request_time_header"] == "X-Request-Start"


@pytest.mark.parametrize("value", ["X Request", "X-Request:", "a\"b", "é"])
def test_request_time_header_invalid(monkeypatch, capsys, value):
    _set(monkeypatch, {"CMS_CWS_REQUEST_TIME_HEADER": value})
    with pytest.raises(SystemExit):
        gc.generate_cms_toml()
    assert "CMS_CWS_REQUEST_TIME_HEADER" in capsys.readouterr().err
```

Adapt `_set` and `gc` to the names used in the file.

- [ ] **Step 2: Run and check that they fail.** Run:
  `.venv/bin/python -m pytest -p no:cacheprovider docker/test_generate_config.py -q`

- [ ] **Step 3: Implement**

```python
    request_time_header = _get("CMS_CWS_REQUEST_TIME_HEADER", "")
    if request_time_header and not re.fullmatch(
            r"[A-Za-z0-9-]+", request_time_header):
        print(f"ERROR: CMS_CWS_REQUEST_TIME_HEADER must be a header name "
              f"(letters, digits and '-'), got {request_time_header!r}.",
              file=sys.stderr)
        sys.exit(1)
```

Render `request_time_header = {_toml_str(request_time_header)}` as the last
key of `[contest_web_server]`, and import `re` if it is missing.

In `.env.example`, in the reverse-proxy section after
`CMS_NUM_PROXIES_USED`, add a commented, explained entry:

```
# Name of the header in which the reverse proxy writes when it received each
# request, so that submissions that reach the proxy before the contest stop
# are accepted even when CMS is busy. Only with a proxy that overwrites the
# header (see docs/docker-deployment.md). Empty: off.
# CMS_CWS_REQUEST_TIME_HEADER=X-Request-Start
```

- [ ] **Step 4: Run the tests**, then the whole file. All pass.

- [ ] **Step 5: Commit**

Message: `feat(docker): configure the CWS request time header` (+ `Refs #5`).

---

### Task 5: Documentation and the Spanish manual

**Files:**
- Modify:
  - `docs/docker-deployment.md`: the Step 2 table at :35-42 and the
    "## Ports" section at :80-93
  - `docs/docker-scripts.md`: the nginx block at :198-233
  - `docs/contest-day.md`: the CWS bullet at :23-25 and "## At the end"
    item 1 at :137-145
- Modify: the matching `docs/locale/es/LC_MESSAGES/*.po` files
  (`docker-deployment.po`, `docker-scripts.po`, `contest-day.po`)

- [ ] **Step 1: English edits**
  - **docker-deployment.md table:** a row for `CMS_CWS_REQUEST_TIME_HEADER`
    (optional, default empty) linking to a new subsection.
  - **docker-deployment.md new subsection** "Submissions at the contest
    stop", after "## Ports". It must cover:
    - what the setting does;
    - the safety rule (only behind a proxy that overwrites the header; CWS
      ports on 127.0.0.1; the same clock);
    - the 60 s bound;
    - the Caddy line, inside `reverse_proxy`:
      `header_up X-Request-Start "t={time.now.unix_ms}"`;
    - the `.env` line `CMS_CWS_REQUEST_TIME_HEADER=X-Request-Start`;
    - how to check it works: CWS logs "arrived N s before its handler ran
      (source: header)" under load.
  - **docker-scripts.md:** in the nginx block, add
    `proxy_set_header X-Request-Start "t=${msec}";` with one sentence after
    the block.
  - **contest-day.md:**
    - The CWS bullet: with the header on, a busy server no longer refuses
      submissions made before the stop. Without it, keep 4 CWS processes.
    - "At the end" item 1: the submission time is when the request reached
      the proxy (with the header) or the server (without it). A refused
      submission now shows "Envío no aceptado: La competencia ya
      finalizó.". If the header is not configured, set the contest stop
      about one minute after the announced end.

- [ ] **Step 2: Update the Spanish catalogs** with the recipe in
  `docs/locale/es/GLOSSARY.md`:

  ```bash
  rm -rf /tmp/pot
  .venv/bin/sphinx-build -W --keep-going -b gettext docs /tmp/pot
  .venv/bin/sphinx-intl update -p /tmp/pot -l es -d docs/locale
  ```

  Install the docs requirements into the venv if the tools are missing (see
  `.github/workflows/docs.yml` / `.readthedocs.yml` for the package list).
  Translate every new or fuzzy entry, following the GLOSSARY rules:
  - address the reader as "tú";
  - straight double quotes;
  - quote CWS strings exactly as in `cms/locale/es/LC_MESSAGES/cms.po`;
  - keep every inline `code` span verbatim.

  Then remove the `#, fuzzy` lines.

- [ ] **Step 3: Check**

```bash
.venv/bin/python -m pytest -p no:cacheprovider docs/tests -q
.venv/bin/python docs/check_translations.py /tmp/pot docs/locale/es/LC_MESSAGES
.venv/bin/sphinx-build -W --keep-going -b html -D language=es docs /tmp/html-es
```

Expected: all three succeed with no warnings.

- [ ] **Step 4: Commit**

Message: `docs: explain the request time header for submissions at the stop` (+ `Refs #5`).

---

### Task 6: Load harness: simulate the proxy header

**Files:**
- Modify:
  - `cmstestsuite/loadtest/driver.py` (`Driver.http` at :120-146;
    `submit()` at :195-245; CLI at :425-445)
  - `cmstestsuite/loadtest/config/cms.toml.tmpl` (end of
    `[contest_web_server]`, :62-74)
  - `cmstestsuite/loadtest/render_config.py` (`values()` at :59-83; argparse
    at :92-100)
  - `cmstestsuite/loadtest/run.sh` (flag parsing at :32-49; the
    `render_config.py` call at :115-118; the driver call at :334-338; the
    usage header at :5-8)
  - `cmstestsuite/loadtest/README.md` (options table at :128-142)
  - `.github/workflows/loadtest.yml` (env knobs at :38-50; the Run step at
    :113-121)
- Test: `cmstestsuite/unit_tests/loadtest/render_config_test.py`

**Interfaces:**
- Produces:
  - `render_config.values(..., request_time_header: str = "")`.
  - CLI `render_config.py --request-time-header NAME`.
  - `driver.py --request-time-header NAME`, which sends
    `NAME: t=<ms since epoch>` on submit POSTs.
  - `run.sh --request-time-header NAME`.
  - Workflow knob `LOAD_REQUEST_TIME_HEADER` (default `""`).

- [ ] **Step 1: Failing test** in `render_config_test.py`:

```python
    def test_request_time_header_rendered_only_when_given(self):
        import tomllib
        here = os.path.join(os.path.dirname(__file__), "..", "..",
                            "loadtest", "config")
        template = open(os.path.join(here, "cms.toml.tmpl")).read()
        base = dict(db_url="postgresql+psycopg2://cms:pw@db:5432/cmsdb",
                    secret_key="0" * 32, rws_password="pw", workers=2,
                    cws=2, two_phase=False)
        off = tomllib.loads(render_config.render(
            template, render_config.values(**base)))
        self.assertNotIn("request_time_header", off["contest_web_server"])
        on = tomllib.loads(render_config.render(
            template, render_config.values(
                **base, request_time_header="X-Request-Start")))
        self.assertEqual(on["contest_web_server"]["request_time_header"],
                         "X-Request-Start")
```

- [ ] **Step 2: Implement**
  - **Template:** add a marker line `@CWS_EXTRA@` as the last line of
    `[contest_web_server]`.
  - **`values()`:** gains `request_time_header: str = ""`, and
    `CWS_EXTRA` is `""` or `'request_time_header = "%s"'` (the name is
    validated against `[A-Za-z0-9-]+`, raising `ValueError` otherwise).
  - **argparse:** gains `--request-time-header` (default `""`).
  - **`driver.py`:** `Driver.http(..., headers=None)` passes `headers` to
    `session.request`. `submit()` sends
    `{args.request_time_header: "t=%d" % (time.time() * 1000)}` when the
    option is set; take the time immediately before the POST. The CLI
    gains `--request-time-header` (default `""`).
  - **`run.sh`:** a `--request-time-header NAME` flag (default empty)
    passed to both `render_config.py` and `driver.py` only when non-empty.
    Document it in the usage header and the README options table: it
    simulates a front proxy that stamps the arrival time; the upstream
    target ignores the key with a warning.
  - **`loadtest.yml`:**
    - add the knob `LOAD_REQUEST_TIME_HEADER: ""`, commented as above (it
      is not a compose variable name);
    - pass `--request-time-header "$LOAD_REQUEST_TIME_HEADER"` in the Run
      step only when it is non-empty;
    - check with `uvx -q --from yamllint yamllint -d relaxed` and
      `uvx actionlint-py`.

- [ ] **Step 3: Run**
  - the loadtest unit tests:
    `.venv/bin/python -m pytest -p no:cacheprovider cmstestsuite/unit_tests/loadtest -q`,
    with `CMS_CONFIG` set because the setup tests need it;
  - `bash -n` and `shellcheck` on `run.sh`;
  - pyflakes.

- [ ] **Step 4: Commit**

Message: `test(loadtest): let the driver stamp the arrival time like a proxy` (+ `Refs #5`).

---

### Task 7: Validation run on GitHub and report

**Files:**
- Create: `cmstestsuite/loadtest/results/2026-10-07-arrival-time/`
  (`summary.md`, `metrics.json` and the process census of each run, as in
  `results/2026-10-07/`)
- Create: `docs/superpowers/reports/2026-10-07-arrival-time-validation.md`
  (short)

- [ ] **Step 1: Launch**
  - From a detached worktree of this branch's head, change
    `.github/workflows/loadtest.yml` in one throwaway commit:
    `LOAD_CWS: "2"`, `LOAD_WORKERS: "8"`, `LOAD_PROFILE: portable`,
    `LOAD_REQUEST_TIME_HEADER: "X-Request-Start"`.
  - Push it to `loadtest/arrival-time` (do not commit that change on the
    branch).
  - Watch the run with bounded waits:
    `timeout 540 gh -R AresLOLXD/cms run watch <id>`, repeated.

- [ ] **Step 2: Acceptance**
  - **On every fork run:** 0 submissions refused that the driver sent
    before the stop, 0 HTTP errors, 0 score mismatches. The 2026-10-07
    baseline on the same settings refused 11 and 28 in-time submissions.
  - **CWS logs** show "arrived ... (source: header)" lines near the stop.
  - **Upstream runs** keep their old behaviour; they are the control.
  - If a fork run still refuses an in-time submission, investigate with
    the run's `requests.jsonl` and `submissions.jsonl` and the CWS logs
    before changing anything. Report it; do not weaken the acceptance.
  - Score mismatches caused by host pauses (wall-clock TLE, see the
    2026-10-07 investigation) are not this task's acceptance. List them
    separately.

- [ ] **Step 3: Record the results**
  - Download the artifacts and copy the small files of each run into the
    results directory. Never copy `users.json`, `db_export.json` or logs.
  - Write the short report:
    - settings;
    - refused in-time submissions, fork vs upstream vs the 2026-10-07
      baseline;
    - the arrival delays seen;
    - anything unexpected.

  No host names, paths or budget details.

- [ ] **Step 4:** Delete the remote `loadtest/arrival-time` branch and
  commit the results and the report:
  `docs(loadtest): validate the arrival-time phase check on GitHub` (+ `Refs #5`).
