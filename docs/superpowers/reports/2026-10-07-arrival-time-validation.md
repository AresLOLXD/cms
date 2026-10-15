# Arrival-Time Phase Check: Validation on GitHub-Hosted Runners

**Date:** 2026-10-07
**Issue:** #5
**Run:** https://github.com/AresLOLXD/cms/actions/runs/37659449108 (4 repeats,
each on its own `ubuntu-24.04` runner with 4 vCPUs, fork and upstream
one after the other, alternating order).
**Code under test:** `9c7947d5` of `feat/cws-arrival-time`. The pushed commit
changed only the four knobs of `.github/workflows/loadtest.yml` (its sha,
`42fcdb0d`, is the one in each `image.txt`); that change is not on the branch.
The small per-run results are in
`cmstestsuite/loadtest/results/2026-10-07-arrival-time/`.

## Settings

Same as the 2026-10-07 baseline run (37599072647), plus the header: 250 users
(175 + 75), contest 1500 s, end burst 300 s, 2 ContestWebServer shards,
8 Workers, `portable` profile, `UPSTREAM_REF` `cc9dfafb`, and
`LOAD_REQUEST_TIME_HEADER=X-Request-Start`. The driver stamps
`X-Request-Start: t=<ms>` on every submit POST, as a front proxy would. The
fork reads it (`request_time_header`); upstream warns about the unknown key and
ignores it, so upstream is the control. The baseline sent no header and had 3
repeats.

## How the refusals were counted

`analyze.py` counts as rejected every driver submission without a
`submission_id` redirect. That is not the same as "refused although sent before
the stop": the driver checks `time < stop - 2` and only then builds the submit,
and in 30 % of the submits it first loads the task statement. At the end of the
contest that GET took 8-28 s, so the POST was created after the stop. Each
submission was therefore split by `t_submit` (the instant the POST was
created, and the value of the header) against the stop:

- created before the stop: must be accepted;
- created after the stop: refused by design, whatever the server does.

## Results

Rejected = `submissions_rejected` of `analyze.py`. "In flight" = POSTs created
before the stop whose response came after it.

| run | sent | rejected | of which created before the stop | after the stop | in flight at the stop (accepted) | HTTP errors | score mismatches |
|---|---|---|---|---|---|---|---|
| fork 1 | 1094 | 17 | **0** | 17 | 37 (37) | 0 | 0 |
| fork 2 | 1042 | 22 | **0** | 22 | 46 (46) | 0 | 0 |
| fork 3 | 1094 | 17 | **0** | 17 | 45 (45) | 0 | 2 (host pause) |
| fork 4 | 1129 | 15 | **0** | 15 | 39 (39) | 0 | 0 |
| upstream 1 | 1108 | 1 | 0 | 1 | 26 (26) | 0 | 1 (host pause) |
| upstream 2 | 1053 | 52 | 33 | 19 | 92 (59) | 201 | 0 |
| upstream 3 | 1151 | 43 | 31 | 12 | 61 (30) | 165 | 0 |
| upstream 4 | 1095 | 52 | 37 | 15 | 60 (23) | 385 | 0 |

The same split on the baseline run (no header):

| target | rejected (repeats 1/2/3) | created before the stop | after the stop |
|---|---|---|---|
| fork | 11 / 28 / 0 | 9 / 20 / 0 | 2 / 8 / 0 |
| upstream | 4 / 20 / 49 | 0 / 16 / 36 | 4 / 4 / 13 |

- **Fork:** 0 in-time submissions refused in all four repeats (baseline: 9 and
  20 in two of three). All 167 submits in flight at the stop were accepted, and
  no accepted submission has a timestamp after the stop. 0 HTTP errors. Every
  one of the 71 refused submissions was created 0.1-17 s after the stop, and
  every one was preceded by a task statement GET that began 3-17 s before the
  stop (so it passed the guard) and ended just before the POST.
- **Upstream (control):** unchanged behaviour. It refuses in-flight submits at
  the stop (33, 31 and 37 in repeats 2-4; 25, 17 and 30 of those were HTTP 500
  from CWS pool exhaustion, as in the baseline), and its logs have no arrival
  line.
- **Not this task's acceptance, listed separately:** fork 3 had 2 score
  mismatches (a108 `libre` 50 -> 25, a019 `cadena` 100 -> 80) and upstream 1 had
  1 (b054 `sumab` 100 -> 60). In each, one or two testcases ran 4-8 s of wall
  clock (7.9 s at most) where the others take about 1 s, the host pauses of the
  2026-10-07 investigation (wall-clock TLE). The echo latency of CWS (fork 3) or
  ES (upstream 1) spiked above 5 s in the same runs.

## Arrival delays seen (fork CWS logs)

The CWS logs an INFO line only when the arrival is more than 1 s before the
handler ran. The line reads "Request POST ... arrived N s before its handler
ran, as its X-Request-Start header says" (not "source: header").

| run | lines | median | p95 | max | arrived before the stop, handled after it |
|---|---|---|---|---|---|
| fork 1 | 138 | 4.0 s | 10.2 s | 20.7 s | 34 (up to 20.7 s late) |
| fork 2 | 202 | 2.8 s | 16.9 s | 25.4 s | 26 (up to 24.7 s) |
| fork 3 | 362 | 4.0 s | 15.0 s | 23.3 s | 37 (up to 23.3 s) |
| fork 4 | 246 | 3.5 s | 11.1 s | 20.3 s | 37 (up to 20.3 s) |

Those last submissions are the ones the old check refused. No line says
"clamped": the longest delay (25.4 s) is far below the 60 s limit. The
upstream runs have no such line.

## Verdict

- Met on all four fork runs: no submission created before the stop was refused,
  0 HTTP errors, the arrival lines are in the CWS logs near the stop, and
  upstream behaved as before.
- **Not met as the brief states it:** `submissions_rejected` is 15-22 on the
  fork, not 0, because the driver itself sends 15-22 submissions after the
  stop (see "How the refusals were counted"). The baseline figure "11 and 28
  in-time" had the same flaw: 9 and 20 were in time. Fork 3 also has the 2
  host-pause mismatches above.

## Unexpected

- The driver's `time < stop - 2` guard does not cover the statement GET that
  follows it, so the harness cannot make "rejected = 0" the acceptance on a
  loaded runner. The harness fix landed after these runs: the driver checks
  the time again just before the POST and no longer sends POSTs created after
  the stop, so `submissions_sent` falls by about 15-22 compared with these
  runs; `analyze.py` has a new metric, `submissions_rejected_in_time`
  (rejections whose `t_submit` is before the stop), which `metrics.json` and
  `compare.py` include. The results in this report were not recomputed with
  the fixed harness: that needs a new run (or the artifacts of these runs).
- The fork's end-burst submit p95 is 11-17 s, against 2.1-9.0 s in the
  baseline. CWS event loop stalls (echo max 5.6-9.4 s, in both runs) dominate
  this tail and the runner-to-runner spread is large, so this run does not show
  that the feature caused it. Nothing here tests that.
