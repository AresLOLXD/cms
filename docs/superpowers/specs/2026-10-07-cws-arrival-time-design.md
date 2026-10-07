# Accept Last-Second Submissions: Arrival-Time Phase Check — Design Doc

**Date:** 2026-10-07
**Issue:** #5 (wave 1 of `2026-10-06-post-10-10-roadmap-design.md`)
**Status:** Designed and approved autonomously. The user asked for the
backlog to advance without stopping, always through brainstorming.
Decisions marked *(autonomous)* took the recommended option and can be
revisited.

## Problem

A submission sent before the contest stop can be refused as late, and the
contestant is not told. The time that decides the phase is taken when the
request handler is built (`CommonRequestHandler.__init__`,
`cms/server/util.py:257`, `self.timestamp = make_datetime()`). Under load
the request waits before that point.

**Load-test evidence (#7, 2026-10-07):**

- **16-thread host, 1000 harness users, 4 CWS:** 24 submissions sent
  0.02-3.09 s before the stop were refused.
- **4-vCPU GitHub runner, 250 harness users, 2 CWS:** 11 and 28 were
  refused in two of three fork runs. The refused ones left the client up to
  9.2 s before the stop, waited in the queue (end-burst submit p95 9 s,
  max 22 s), and reached the handler after the stop. Every submission whose
  handler started before the stop was stored.
- **CWS's own work per request was 10-20 ms.** The wait happens before
  Tornado reads the socket: the event loop is busy or the host has no CPU
  to give it. A timestamp taken inside the CWS process, even Tornado's own
  `HTTPServerRequest` start time, is late in exactly these cases.

**The silent part:** `actual_phase_required` (`phase_management.py`)
redirects a refused POST to the contest page with no notification (the
code has a `TODO maybe return some error code?`). The contestant believes
the submission went through.

## Goal

1. A submission that reached the deployment's front proxy before the stop
   is accepted.
2. Without a front proxy, accept it when the CWS process received it
   before the stop.
3. Do not change the contest rules: no submission that arrived after the
   stop is accepted.
4. A refused submission always tells the contestant why.

## Approaches Considered

- **A (chosen): arrival time with bounded trust.** The time that decides
  the phase, and that is stored on the submission, is the earliest
  credible time the request was seen: the handler time, Tornado's request
  start time, and, only if the operator turns it on, a time a trusted proxy
  wrote in a header. It is bounded so that it can never move more than a
  fixed amount into the past. *(autonomous)*
- **B (rejected): a grace window**, accepting submissions for N seconds
  after the stop. It is simpler, but it changes the rules: a submission
  that really arrived late would be accepted, and contestants who know
  about it could use it.
- **C (not enough alone): remove the cause,** that is, move CWS's DB work
  off the event loop (#22). This shortens the stalls, but it cannot help
  when the host itself has no CPU, as on the GitHub runner. #22 stays in
  wave 3.

## Design

### 1. The request's arrival time

A new helper, `request_arrival_time(handler_time, tornado_start, header_value, max_skew)`,
is pure and unit-tested. It returns the effective time:

- **Candidates:**
  - `handler_time`: `make_datetime()` in `__init__`, as today.
  - `tornado_start`: the start time Tornado recorded when it read the
    request headers, computed as `handler_time` minus the elapsed time from
    the public API `request.request_time()`. Using the elapsed time, not the
    absolute clock, keeps tests that shift `make_datetime()` consistent. It
    never trusts a value later than `handler_time`.
  - `header_time`: parsed from the configured header, only when the
    service has a header configured.
- **Result:** the minimum of the candidates that parse and that are not
  later than `handler_time`, clamped to no earlier than
  `handler_time - max_skew`.
- **`max_skew`:** a module constant of 60 s. A proxy clock that is wrong,
  or a forged header that slipped through, can move a submission at most
  60 s back. Legitimate queueing in the tests was below 25 s.
  *(autonomous)*
- **Header formats accepted:**
  - a Unix time in seconds, with or without decimals (`1696698123.456`,
    nginx `$msec`);
  - a Unix time in milliseconds, an integer at or above 10^11 (Caddy
    `{time.now.unix_ms}`);
  - either one with an optional `t=` prefix (the `X-Request-Start`
    convention).

  Anything else is ignored with a debug log, never an error.

`CommonRequestHandler.__init__` sets `self.timestamp` to this effective
time, so every check that uses `self.timestamp` sees the arrival time:
- the phase computation in `ContestHandler`;
- `accept_submission`, `accept_user_test` and `accept_token`;
- the minimum-interval checks;
- the timestamp stored on the submission.

`self.handler_time` keeps the old value for logging and for the clock
and countdown shown to the contestant (`render_params()["now"]`), which
must not run behind by the queueing delay. AWS uses the same
base class with no header configured, so for AWS only Tornado's start time
can move the time, by milliseconds.

### 2. Configuration

- **`cms.toml`:** new key `contest_web_server.request_time_header`
  (string, default empty = off). When set, CWS reads that header for
  `header_time`. It applies only to CWS.
- **Docker (`docker/generate_config.py`):** a new env var
  `CMS_CWS_REQUEST_TIME_HEADER` (default empty). Its value is validated as
  a header name (token characters only); an invalid value stops
  config generation with an error, as the other `CMS_*` checks do. A test
  is added to `docker/test_generate_config.py`.
- **Safety rule, documented:** turn it on only when every request reaches
  CWS through a proxy that **sets** (overwrites) that header. In the Docker
  deployment the CWS ports listen on 127.0.0.1 only (since 2026-10-04), so
  every request comes through the front proxy. Example for Caddy, inside
  the `reverse_proxy` block:

  ```
  header_up X-Request-Start "t={time.now.unix_ms}"
  ```

  `header_up` replaces a value sent by the client. The proxy and CWS must
  share a clock: the same host, or NTP.

The production change (`.env` plus Caddy) is the operator's decision and is
not made by this work. Until it is, behaviour matches today's, except that
Tornado's start time applies.

### 3. Visible refusal

- **Web submits refused by the phase check:** `actual_phase_required` gains
  an optional message argument. When the request is a POST from the web UI
  and is refused, it adds an error notification before redirecting.
  `SubmitHandler` and the user-test submit handler pass "The contest is
  over: this submission was not accepted." for the phases after the
  contest, and "The contest has not started yet" for the phases before it.
  Phase codes are mapped in one place.
- **The API path:** unchanged. It already answers 403 JSON with an error
  string.
- **The existing in-phase refusal:** `allow_unofficial_submission_before_analysis_mode`
  makes `SubmitHandler.post` redirect silently at its first check. That
  check gets the same notification, with its own message.
- **Translations:** all strings are wrapped with `N_()`. The Spanish
  catalog of CWS gets the new msgids (there is a check that untranslated
  strings fail).

### 4. Logging

When the effective time differs from `handler_time` by more than 1 s, CWS
logs an INFO line with the request path, the delay and the source used
(Tornado or header). Operators can then see queueing at the stop in the
CWS log.

## Testing

- **Unit tests for `request_arrival_time`:**
  - each source alone;
  - minimum selection;
  - a candidate later than `handler_time` is ignored;
  - the clamp at `max_skew`;
  - every header format, plus garbage, an empty value and a negative value.
- **Handler test (existing CWS handler test patterns):**
  - a submit whose arrival time is before the stop, while the handler time
    is after it, is accepted;
  - the stored timestamp is the arrival time;
  - with the header off, a header value is ignored.
- **Notification test:** a POST submit after the stop produces the error
  notification and the redirect. The API path still gets the 403 JSON.
- **`docker/test_generate_config.py`:** default empty, a valid name is
  rendered, an invalid name exits with the error.
- **Load test:**
  - the harness driver gets an option to send `X-Request-Start` with its
    own send time, simulating a front proxy;
  - `run.sh` gets a `--request-time-header` flag that renders the new
    `contest_web_server.request_time_header` key into the harness
    `cms.toml` template (left out when the flag is not given, so upstream
    runs are unaffected);
  - one GitHub run at 250 users, 2 CWS, 8 Workers on the fork, with the
    option on, must show 0 submissions refused that were sent before the
    stop.

## Documentation

- **`docs/docker-deployment.md`:** the new variable, the safety rule and
  the Caddy snippet.
- **`docs/contest-day.md`:** the effect at the stop, and that the stop
  margin workaround is no longer needed once the header is on.
- **Manual:** the matching `.po` entries in `docs/locale/es/LC_MESSAGES`
  for the English changes (the docs CI checks them).

## Out of Scope

- #22 (CWS DB work off the loop, admission control): wave 3.
- A grace window or any other rule change.
- Changing the production Caddy or `.env`: the operator decides after the
  contest.
- AWS's own header.

## Risks

- **A misconfigured proxy that forwards a client-supplied header** lets a
  contestant backdate a submission by up to 60 s. Mitigations: the setting
  is off by default, the safety rule is documented, and CWS listens only on
  127.0.0.1 in the Docker deployment.
- **Clock skew between the proxy and CWS** shifts times by the skew,
  bounded by the clamp. Documented.
