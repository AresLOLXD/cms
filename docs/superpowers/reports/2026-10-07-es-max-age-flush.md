# Issue #6, ES max-age flush: analysis of the load tests (control, N = 1 s, N = 2 s)

Date: 2026-10-07. Read-only analysis of 12 GitHub Actions runs (4 per variant)
plus the 4 runs of the 10-10 rehearsal as extra controls.
GitHub Actions runs (workflow `loadtest`): control (beta f3e181ac) 37687314224,
N = 1 s (82d4bf64) 37687314073, N = 2 s 37687314273; rehearsal 37647110989.

## Verdicts

1. **Phase split.** The p50 gain is not uniform. It is large and the same in
   every run during the steady phase, and it holds for the first ~2-3 minutes
   of the end burst. It goes away in the last ~2 minutes of the burst and in
   the drain, where latency depends on the backlog and the runner's speed.
   - Steady phase, median of 4 runs: p50 20.5 s (control), 5.4 s (N=1), 8.9 s (N=2).
     p95: 52.4, 14.1 and 24.5 s.
   - First half of the burst, per run: p50 18.6-32.3 s (8 control-equivalent runs),
     9.0-11.0 s (N=1), 8.3-19.5 s (N=2).
   - Burst submissions scored after the stop: no gain, beyond what the runner
     explains.
2. **Is the worse p95/drain real? No.** The difference comes from which runners
   each variant got. EPYC 7763 is the slowest model. Control got one runner of
   each of the 4 CPU models. N=1 got three EPYC 7763 runners. N=2 got two
   EPYC 7763 runners plus a slow EPYC 9V74 instance.
   - Compared on the same CPU model, N=1 and N=2 drain as fast or faster.
   - Regressing on runner speed removes the gap: the median per-testcase worker
     time explains 84 % of the drain variance over the 8 control-equivalent runs.
     Against that model, the effect on drain is -0.1 s for N=1 (95 % CI ±21 s)
     and +4 s for N=2 (CI -17 to +25 s).
   - Mechanism: the capacity lost to `post_finish_lock` does not grow with max
     age. Workers are fed better in the drain.
   - No host pauses: no all-service log silence over 0.65 s, and no monitor gaps.
3. **Recommendation: N = 1 s, as on the branch.** Do not add a
   "small buffer only" variant. Do the second item of issue #6 (release the
   worker before taking `post_finish_lock`) as a separate change: it is the
   lever for the drain, since all variants lose ~12-14 % of worker capacity in
   the burst and drain to waiting on that lock.

## Method

**Data.** Every run uses `profile full` with the same scenario: 250 users,
6 Workers, 4 CWS, a 1500 s contest and a 300 s end burst, each on its own
4-vCPU `ubuntu-24.04` runner.

| variant | image commit | ES constants |
|---|---|---|
| control | `1aecb87d` (beta `f3e181ac` plus a loadtest commit) | no max age |
| N=1 | `7ea6c36c` (on `82d4bf64`) | `MAX_RESULT_AGE_SECONDS = 1` |
| N=2 | `d7ec067c` | `MAX_RESULT_AGE_SECONDS = 2` |
| rehearsal | `75bc50c4` | no max age |

- In all four builds `RESULT_CACHE_SIZE = 100` and
  `MAX_FLUSHING_TIME_SECONDS = 2`.
- `82d4bf64` behaves the same as the branch tip `8e1f5c08`: the later commit
  only merges the max-age check into the same `if`.
- The rehearsal build differs from control only in CWS (the arrival-time
  feature) and in a driver fix (no submit POST after the stop). It has no
  ES/Worker change, so I use its 4 runs as extra controls ("control-equivalent",
  8 runs).

**Definitions.**

- **Latency.** Server latency is `scored_at - timestamp` from `db_export.json`,
  joined with `submissions.jsonl` by `opaque_id`. This is the same as
  `analyze.py`.
- **Steady phase.** Driver phases `start_burst` and `steady`, i.e. submitted
  before stop - 300 s. The driver's phase label matches the submit time in
  every run.
- **End burst.** Driver phase `end_burst`. I split it two ways:
  - by submit time: first half and second half of the 300 s;
  - by outcome: scored before the stop, or scored after the stop.
- **"After the stop".** No submission is created after the stop (the driver
  does not POST after it; there is 1 in-time rejection, in n2-2). So I report
  the burst submissions scored after the stop, plus `drain_after_stop_s`.
- **Windows for internals.**

  | window | span |
  |---|---|
  | steady | start + 60 s to stop - 300 s |
  | burst | stop - 300 s to stop |
  | drain | stop to the last `scored_at` |

**Flushes.** Each FlushingDict flush is one `write_results` call. It runs as
`_write_results_sync` on one executor thread and holds `post_finish_lock`
throughout.

- The ES log marks it with `Starting commit process...` and `Done`, on the same
  `asyncio_N` thread.
- Results per flush is the count of `Writing result to db for` lines in between.
- Objects per flush come from `Ending operations for N objects`.
- Lock duty is the share of the window during which a flush held the lock.

**Worker timelines.** I joined, per shard and in order:

1. ES `Asking worker N to ...`;
2. Worker-N `Starting job group.` and `Finished job group.`;
3. the ES line `` `<first op>' succeeded. ``, logged in `_action_finished_sync`
   after the lock is taken and `release_worker` has run;
4. the next `Asking worker N`.

The first operation of every group matched in all 16 runs (0 mismatches). Each
worker's time splits into four parts:

| part | from | to |
|---|---|---|
| busy | worker start | worker end |
| result wait | worker end | ES accepts the result (mostly waiting for `post_finish_lock`) |
| redispatch | ES accepts | next ask (empty queue: work held back until the next flush) |
| delivery | ask | worker start (job-group build in a thread, plus RPC) |

**Runner speed.** The median wall time of one evaluate job (Worker `Starting job`
to `Finished job`) in the steady phase, when the host is ~30-40 % busy. It
tracks the CPU model and also catches a slow instance.

**Host signals.**

- `docker stats`: sum of all containers, as a percentage of 400.
- Monitor: ES CPU, ES RPC echo, gaps between samples.
- The largest silence across all service logs (the LogService merged log)
  during burst and drain; a host pause stops every service at once.
- CPU steal is not recorded anywhere in the artifacts.

**Scripts** (all stdlib; one-off analysis scripts, not kept in the repository, so the
numbers here come from the run artifacts of the runs above):

| script | what it does |
|---|---|
| `analyze_runs.py` | per-run JSON and timelines in `results/` |
| `table1.py`, `tables_md.py` | tables |
| `bins.py` | latency by submit-time bin |
| `drain_model.py`, `matched.py` | speed regression and CPU-matched comparison |
| `flushcost.py` | lock ms per result and per object |
| `idle_pause.py` | idle gaps and log silences |

## Q1. Latency by phase
### Q1 per variant: median of the 4 runs

| variant | steady p50 | steady p95 | end burst p50 | end burst p95 | burst scored-before-stop p50 / p95 | burst scored-after-stop p50 / p95 (n) | overall p50 / p95 | drain s |
|---|---|---|---|---|---|---|---|---|
| control | 20.5 | 52.4 | 32.0 | 87.5 | 24.3 / 59.3 | 69.4 / 99.1 (158) | 23.6 / 74.7 | 100 |
| n1 | 5.4 | 14.1 | 33.3 | 117.9 | 15.5 / 55.6 | 93.7 / 122.5 (188) | 7.8 / 102.2 | 124 |
| n2 | 8.9 | 24.5 | 43.8 | 129.2 | 21.9 / 61.9 | 98.6 / 138.1 (198) | 13.0 / 110.8 | 142 |
| rehearsal | 21.1 | 54.9 | 41.0 | 121.9 | 23.5 / 55.3 | 96.1 / 131.1 (182) | 25.0 / 107.8 | 134 |

How to read it:

- Steady phase: N=1 cuts p50 by 75 % and p95 by 73 % compared with control
  (20.5 to 5.4 s, and 52.4 to 14.1 s). N=2 cuts them by 57 % and 53 %.
- Rehearsal (an older control on the same code paths) matches control in the
  steady phase. Its burst numbers are worse than control's because it got two
  EPYC 7763 runners.
- End burst and overall p95 come out in the "wrong" order. That is runner
  mix, not the variant (see Q2).

### Q1 per run: server submit->scored latency by phase (s), p50 / p95 (n)

| run | CPU | steady (submitted before stop-300) | end burst (all) | burst, scored before stop | burst, scored after stop | burst 1st half | burst 2nd half | drain s |
|---|---|---|---|---|---|---|---|---|
| control-1 | Xeon 8370C | 20.8 / 54.9 (892) | 34.7 / 88.8 (510) | 24.8 / 60.9 (353) | 72.1 / 100.0 (157) | 24.5 / 58.2 (206) | 51.8 / 94.6 (304) | 99 |
| control-2 | EPYC 7763 | 20.7 / 53.1 (785) | 57.0 / 137.2 (482) | 32.2 / 73.9 (280) | 109.5 / 146.4 (202) | 26.6 / 62.8 (162) | 86.1 / 142.1 (320) | 153 |
| control-3 | Xeon 6973P | 18.1 / 47.6 (811) | 28.2 / 64.3 (512) | 23.8 / 57.8 (377) | 48.2 / 72.6 (135) | 19.6 / 48.7 (187) | 34.1 / 67.6 (325) | 70 |
| control-4 | EPYC 9V74 | 20.2 / 51.7 (832) | 29.3 / 86.1 (506) | 20.5 / 50.1 (348) | 66.6 / 98.3 (158) | 18.6 / 48.7 (172) | 39.9 / 90.1 (334) | 101 |
| n1-1 | Xeon 8370C | 5.1 / 14.2 (902) | 38.8 / 111.4 (530) | 16.1 / 67.5 (337) | 87.1 / 114.8 (193) | 9.0 / 27.5 (177) | 66.7 / 113.4 (353) | 112 |
| n1-2 | EPYC 7763 | 5.0 / 13.7 (842) | 27.7 / 124.5 (507) | 14.1 / 47.4 (323) | 100.2 / 130.3 (184) | 9.2 / 21.8 (181) | 68.4 / 128.0 (326) | 136 |
| n1-3 | EPYC 7763 | 5.6 / 14.6 (873) | 41.2 / 130.3 (508) | 17.1 / 63.8 (310) | 101.2 / 136.1 (198) | 11.0 / 27.8 (186) | 81.8 / 133.6 (322) | 137 |
| n1-4 | EPYC 7763 | 5.6 / 14.0 (881) | 27.3 / 103.1 (478) | 14.9 / 46.8 (317) | 83.1 / 111.1 (161) | 10.5 / 25.7 (162) | 51.9 / 105.2 (316) | 108 |
| n2-1 | Xeon 6973P | 7.4 / 20.6 (896) | 12.9 / 54.8 (513) | 9.3 / 24.6 (382) | 43.5 / 65.6 (131) | 8.3 / 24.5 (175) | 18.4 / 60.0 (338) | 67 |
| n2-2 | EPYC 7763 | 8.9 / 24.4 (862) | 46.7 / 133.9 (504) | 25.5 / 64.8 (300) | 103.9 / 143.9 (204) | 19.5 / 57.9 (169) | 76.2 / 138.3 (335) | 147 |
| n2-3 | EPYC 7763 | 8.9 / 24.9 (848) | 45.4 / 136.7 (498) | 21.9 / 59.0 (296) | 109.2 / 144.2 (202) | 13.3 / 39.1 (173) | 82.0 / 140.1 (325) | 150 |
| n2-4 | EPYC 9V74 | 9.3 / 24.6 (874) | 42.3 / 124.4 (496) | 22.0 / 65.5 (301) | 93.4 / 132.4 (195) | 15.3 / 41.1 (175) | 75.1 / 128.5 (321) | 136 |
| rehearsal-1 | EPYC 7763 | 22.6 / 60.8 (815) | 63.8 / 156.1 (489) | 37.4 / 82.3 (287) | 120.1 / 166.8 (202) | 32.3 / 72.0 (175) | 101.1 / 163.4 (314) | 165 |
| rehearsal-2 | EPYC 7763 | 21.7 / 57.7 (785) | 47.0 / 140.4 (504) | 23.1 / 57.0 (287) | 110.2 / 152.4 (217) | 20.9 / 50.7 (152) | 84.8 / 146.0 (352) | 155 |
| rehearsal-3 | EPYC 9V74 | 20.4 / 52.2 (831) | 31.5 / 92.3 (508) | 20.7 / 53.5 (347) | 74.1 / 99.6 (161) | 20.7 / 54.1 (174) | 46.5 / 96.3 (334) | 111 |
| rehearsal-4 | Xeon 8370C | 19.8 / 52.0 (802) | 35.0 / 103.3 (489) | 23.9 / 51.4 (327) | 82.0 / 109.9 (162) | 21.9 / 52.0 (168) | 50.0 / 108.7 (321) | 114 |

The steady-phase gain is uniform:

- p50 per run: 5.0-5.6 s for N=1 and 7.4-9.3 s for N=2, against 18.1-22.6 s
  for all 8 control-equivalent runs, on every CPU model.
- Corrected for runner speed, the effect on steady p50 is -15.3 s for N=1
  (95 % CI -16.0 to -14.6) and -11.9 s for N=2 (CI -12.6 to -11.2).
- On steady p95: -39.9 s for N=1 (CI -43 to -37) and -30.1 s for N=2
  (CI -33 to -27).

### Latency by submit-time bin

Seconds relative to the stop. Each cell is the median over the runs of the
per-run p50 or p95.

| group | | [-1500,-1200) | [-1200,-900) | [-900,-600) | [-600,-300) | [-300,-240) | [-240,-180) | [-180,-120) | [-120,-60) | [-60,0) |
|---|---|---|---|---|---|---|---|---|---|---|
| control (4) | p50 | 19.2 | 19.4 | 20.4 | 20.5 | 22.5 | 22.3 | 19.9 | 28.6 | 61.0 |
| | p95 | 52.8 | 49.6 | 49.9 | 49.7 | 52.2 | 55.1 | 61.5 | 67.4 | 96.8 |
| control + rehearsal (8) | p50 | 19.7 | 19.8 | 21.3 | 21.5 | 22.3 | 21.5 | 19.3 | 30.5 | 72.8 |
| | p95 | 53.0 | 51.9 | 54.3 | 51.3 | 53.3 | 55.3 | 57.1 | 78.2 | 104.4 |
| N=1 (4) | p50 | 5.4 | 5.1 | 5.3 | 5.7 | 8.4 | 9.3 | 18.0 | 39.4 | 87.7 |
| | p95 | 15.3 | 13.5 | 13.4 | 14.8 | 21.2 | 19.5 | 54.1 | 96.3 | 122.0 |
| N=2 (4) | p50 | 8.6 | 8.4 | 8.9 | 8.8 | 10.8 | 14.1 | 25.7 | 39.3 | 96.3 |
| | p95 | 21.9 | 21.9 | 24.6 | 23.8 | 28.8 | 29.3 | 60.0 | 100.4 | 138.1 |
| EPYC 7763 only: control-2, rehearsal-1, rehearsal-2 | p50 | 22.1 | 20.6 | 23.1 | 21.7 | 27.2 | 22.5 | 32.7 | 49.6 | 109.8 |
| | p95 | 56.8 | 57.8 | 60.0 | 56.7 | 56.4 | 65.1 | 86.0 | 117.9 | 152.4 |
| EPYC 7763 only: n1-2, n1-3, n1-4 | p50 | 5.4 | 5.4 | 5.4 | 5.5 | 8.4 | 9.3 | 14.6 | 33.1 | 89.6 |
| | p95 | 15.2 | 14.0 | 13.5 | 14.7 | 19.0 | 20.3 | 39.7 | 107.6 | 129.8 |
| EPYC 7763 only: n2-2, n2-3 | p50 | 8.9 | 8.7 | 8.9 | 8.8 | 10.8 | 17.8 | 26.4 | 41.1 | 104.1 |
| | p95 | 23.6 | 23.6 | 24.6 | 24.1 | 28.8 | 44.3 | 63.9 | 107.2 | 144.0 |

What the bins show:

- The gain is flat over the whole steady phase. It shrinks during the burst
  as the queue backlog builds, which happens in the last ~2-3 minutes.
- On the same CPU model (EPYC 7763), N=1 is still better or equal in every
  bin, including the last minute.
- With all runs pooled, the last two bins look worse for N=1 and N=2. That is
  because more of their runs were on the slow model.

**Why the steady phase gains.** In the steady phase the median gap between
flushes is 5.0-5.5 s for control, 2.5-2.8 s for N=2 and 1.4 s for N=1 (from
`flushstats.py`).

- Without max age, flushes happen at 100 results: a steady stream of results
  never leaves 2 s of quiet.
- With two-phase screening and `depends_on`, each submission goes through about
  4 flush-gated stages: compile, screening, main tests, then dependent
  subtasks.
- 4 stages times the ~3.6 s shorter gap is about 15 s, which is the p50 gain
  measured.

## Q2. Is the worse p95 and drain real? No: the runner explains it

### Workload is comparable

- Burst submissions per run: 478-530.
- Burst evaluations per run: 10.6k-11.7k, so every run is within ±5 % of
  11.2k (Q2 work table below).
- No variant got systematically more work. N=1 and N=2 had slightly more
  steady-phase submissions (842-902 against 785-892), which only adds load to
  the variants that look worse.

### Same CPU model, same or better drain

Each cell is drain in s / burst p95 in s / runner speed (median ms per
evaluate job, steady phase).

| CPU model | control + rehearsal: run drain / burst p95 / job ms | N=1 | N=2 |
|---|---|---|---|
| EPYC 7763 | control-2 153 / 137 / 143; rehearsal-1 165 / 156 / 156; rehearsal-2 155 / 140 / 145 | n1-2 136 / 124 / 131; n1-3 137 / 130 / 142; n1-4 108 / 103 / 131 | n2-2 147 / 134 / 143; n2-3 150 / 137 / 136 |
| EPYC 9V74 | control-4 101 / 86 / 130; rehearsal-3 111 / 92 / 121 | - | n2-4 136 / 124 / 152 |
| Xeon 8370C | control-1 99 / 89 / 123; rehearsal-4 114 / 103 / 118 | n1-1 112 / 111 / 112 | - |
| Xeon 6973P | control-3 70 / 64 / 81 | - | n2-1 67 / 55 / 77 |

**The CPU mix explains the medians.**

| variant | runners |
|---|---|
| control | one of each model: 8370C, 7763, 6973P, 9V74 |
| N=1 | three EPYC 7763 (the slowest model) and one 8370C |
| N=2 | two 7763, one 6973P (the fastest model) and n2-4 |

- n2-4 ran on an EPYC 9V74 instance that was ~20 % slower than the other two
  9V74 runners: 152 ms per job against 121/130 ms.
- That is not caused by N=2: the N=2 runs on 7763 have the same per-job time
  as the control runs on 7763.

**Within a CPU model:**

| CPU model | N=1 vs controls | N=2 vs controls |
|---|---|---|
| EPYC 7763 | 108-137 s against 153-165 s | 147-150 s, equal |
| Xeon 6973P | - | 67 s against 70 s |
| Xeon 8370C | 112 s against 99/114 s, inside the control range | - |
| EPYC 9V74 | - | 136 s against 101/111 s, explained by the slow instance |

**Regression on runner speed.** Fitted on the 8 control-equivalent runs:
drain = -47 + 1.32 x (job ms), R² 0.84, residual SD 14 s. Mean residual of the
4 runs of each variant, with a 95 % CI that covers the noise of 4 new runs plus
the uncertainty of the fit:

| metric | N=1 | N=2 |
|---|---|---|
| drain_after_stop | -0.1 s [-21, +21] | +4.1 s [-17, +25] |
| end-burst p95 | +6.4 s [-17, +30] | +4.0 s [-20, +28] |
| overall scored_p95 | +3.5 s [-20, +27] | +1.1 s [-23, +25] |
| end-burst p50 | -8.0 s [-21, +6] | -3.9 s [-17, +9] |

- The raw median gaps (+24 s drain for N=1, +42 s for N=2) are not supported.
- +42 s lies well outside the N=2 interval.
- With the burst-phase job time as the regressor, the drain residuals are
  -11.9 s (N=1) and +0.5 s (N=2).
- `scored_p95` pools every phase. 38 % of the submissions are in the burst, so
  it is in effect a drain metric.

### Q2 per run: work, runner speed, flushes

| run | CPU | subs steady / burst | evaluations steady / burst | worker job p50 ms steady / burst | flushes steady / burst / drain | results per flush p50 steady / burst / drain | flush s p50 steady / burst / drain (p95 burst) | ES lock duty steady / burst / drain | lock ms per result steady / burst |
|---|---|---|---|---|---|---|---|---|---|
| control-1 | Xeon 8370C | 892 / 510 | 19974 / 11164 | 123 / 142 | 190 / 67 / 28 | 90 / 104 / 100 | 0.76 / 1.49 / 2.29 (4.06) | 0.13 / 0.39 / 0.64 | 9.4 / 16.5 |
| control-2 | EPYC 7763 | 785 / 482 | 17478 / 11022 | 143 / 162 | 184 / 61 / 39 | 76 / 102 / 100 | 0.68 / 1.55 / 2.63 (4.51) | 0.11 / 0.41 / 0.62 | 9.3 / 21.5 |
| control-3 | Xeon 6973P | 811 / 512 | 17968 / 11434 | 81 / 87 | 202 / 72 / 23 | 69 / 104 / 100 | 0.46 / 0.92 / 1.79 (2.56) | 0.08 / 0.30 / 0.55 | 6.5 / 12.9 |
| control-4 | EPYC 9V74 | 832 / 506 | 18332 / 11180 | 130 / 125 | 181 / 65 / 29 | 79 / 102 / 100 | 0.55 / 0.85 / 1.95 (2.92) | 0.09 / 0.24 / 0.50 | 7.5 / 10.7 |
| n1-1 | Xeon 8370C | 902 / 530 | 19888 / 11670 | 112 / 141 | 793 / 135 / 39 | 18 / 39 / 86 | 0.17 / 0.61 / 2.22 (3.74) | 0.14 / 0.50 / 0.76 | 10.6 / 24.9 |
| n1-2 | EPYC 7763 | 842 / 507 | 18606 / 11572 | 131 / 155 | 788 / 149 / 51 | 17 / 38 / 55 | 0.17 / 0.60 / 1.66 (2.44) | 0.13 / 0.43 / 0.67 | 10.5 / 22.1 |
| n1-3 | EPYC 7763 | 873 / 508 | 19168 / 11216 | 142 / 168 | 753 / 136 / 47 | 19 / 40 / 74 | 0.20 / 0.67 / 1.94 (3.35) | 0.15 / 0.50 / 0.69 | 11.8 / 25.8 |
| n1-4 | EPYC 7763 | 881 / 478 | 19550 / 10696 | 131 / 157 | 768 / 146 / 39 | 18 / 38 / 73 | 0.19 / 0.62 / 2.07 (2.59) | 0.15 / 0.44 / 0.67 | 11.0 / 22.5 |
| n2-1 | Xeon 6973P | 896 / 513 | 19618 / 11730 | 77 / 86 | 458 / 108 / 24 | 32 / 62 / 100 | 0.22 / 0.47 / 1.62 (1.14) | 0.09 / 0.20 / 0.59 | 6.7 / 8.8 |
| n2-2 | EPYC 7763 | 862 / 504 | 18838 / 11308 | 143 / 166 | 406 / 83 / 40 | 35 / 70 / 100 | 0.35 / 1.24 / 2.40 (3.45) | 0.13 / 0.41 / 0.63 | 10.0 / 21.3 |
| n2-3 | EPYC 7763 | 848 / 498 | 18636 / 11408 | 136 / 158 | 416 / 83 / 41 | 34 / 73 / 100 | 0.33 / 0.91 / 2.41 (3.53) | 0.13 / 0.38 / 0.62 | 10.0 / 20.0 |
| n2-4 | EPYC 9V74 | 874 / 496 | 19170 / 11100 | 152 / 170 | 403 / 86 / 39 | 36 / 62 / 100 | 0.40 / 0.97 / 2.12 (3.78) | 0.15 / 0.39 / 0.58 | 11.2 / 20.8 |
| rehearsal-1 | EPYC 7763 | 815 / 489 | 18142 / 10646 | 156 / 185 | 166 / 60 / 37 | 90 / 103 / 100 | 0.83 / 2.02 / 2.86 (4.63) | 0.12 / 0.47 / 0.63 | 9.8 / 25.8 |
| rehearsal-2 | EPYC 7763 | 785 / 504 | 17552 / 11184 | 145 / 155 | 172 / 58 / 42 | 83 / 102 / 100 | 0.73 / 1.13 / 2.44 (4.51) | 0.11 / 0.31 / 0.61 | 9.4 / 17.0 |
| rehearsal-3 | EPYC 9V74 | 831 / 508 | 18508 / 11222 | 121 / 144 | 189 / 62 / 31 | 80 / 104 / 100 | 0.56 / 1.06 / 2.01 (3.49) | 0.09 / 0.28 / 0.51 | 7.4 / 13.3 |
| rehearsal-4 | Xeon 8370C | 802 / 489 | 17768 / 10788 | 118 / 134 | 190 / 61 / 32 | 70 / 106 / 100 | 0.61 / 1.29 / 2.27 (4.30) | 0.10 / 0.33 / 0.64 | 8.7 / 16.0 |

**Flushes (`write_results` calls).**

| | control | N=1 | N=2 |
|---|---|---|---|
| steady flushes | ~180-200 | ~750-790 (4x) | ~400-460 (2.2x) |
| burst: flushes (results each) | ~60-70 (~100, the size trigger) | ~135-150 (~38-40) | ~83-108 (~62-73) |
| drain | ~100-result batches, `Done` after the batch | similar | ~100 results |
| ES lock duty, burst (median) | 0.35 | 0.47 | 0.39 |
| ES lock duty, drain (median) | 0.58 | 0.68 | 0.61 |
| lock ms per result, steady | 6.5-9.8 | 10.5-11.8 | 6.7-11.2 |

- In the burst, the max age still fires before the buffer reaches 100.
- In the drain N=1 still flushes at age 1 s (55-86 results).
- Lock ms per result in the steady phase is ~20-30 % higher with N=1: there is
  a fixed cost per flush.
- In the burst, lock ms per result is about the same on the same CPU model
  (EPYC 7763: control 17-26, N=1 22-26).
- So max age does make ES hold `post_finish_lock` more of the time. In burst
  and drain, median lock duty rises by +17-34 % with N=1 and by +5-11 % with
  N=2.

### Q2 per run: worker capacity in the end burst and the drain

Fractions of 6 workers x window. busy = Worker 'Starting job group'..'Finished job group'; result wait = 'Finished job group'..ES '`op' succeeded' (ES got post_finish_lock and released the worker); redispatch = that..next 'Asking worker N' (queue empty); delivery = 'Asking'..Worker 'Starting job group' (job-group build + RPC).

| run | CPU | busy burst / drain | result wait burst / drain | redispatch burst / drain | delivery burst / drain | idle gap between groups in burst p50 / p95 / max s | ops per group burst | worker-s busy after stop | tail s (last >=80% busy -> last scored) |
|---|---|---|---|---|---|---|---|---|---|
| control-1 | Xeon 8370C | 0.71 / 0.65 | 0.191 / 0.176 | 0.04 / 0.09 | 0.057 / 0.051 | 0.16 / 1.66 / 9.7 | 8.5 | 388 | 14 |
| control-2 | EPYC 7763 | 0.65 / 0.70 | 0.128 / 0.142 | 0.17 / 0.10 | 0.047 / 0.047 | 0.33 / 3.56 / 16.7 | 8.7 | 639 | 15 |
| control-3 | Xeon 6973P | 0.45 / 0.60 | 0.100 / 0.133 | 0.40 / 0.19 | 0.049 / 0.051 | 0.25 / 4.52 / 18.9 | 7.0 | 252 | 16 |
| control-4 | EPYC 9V74 | 0.58 / 0.73 | 0.110 / 0.115 | 0.27 / 0.10 | 0.044 / 0.039 | 0.08 / 3.01 / 12.3 | 6.0 | 444 | 12 |
| n1-1 | Xeon 8370C | 0.66 / 0.74 | 0.129 / 0.189 | 0.15 / 0.01 | 0.062 / 0.056 | 0.24 / 1.28 / 6.1 | 4.7 | 495 | 6 |
| n1-2 | EPYC 7763 | 0.67 / 0.78 | 0.127 / 0.123 | 0.14 / 0.05 | 0.060 / 0.043 | 0.27 / 1.10 / 4.3 | 4.1 | 634 | 14 |
| n1-3 | EPYC 7763 | 0.72 / 0.79 | 0.121 / 0.130 | 0.11 / 0.03 | 0.056 / 0.044 | 0.25 / 1.22 / 3.7 | 4.7 | 647 | 11 |
| n1-4 | EPYC 7763 | 0.68 / 0.76 | 0.127 / 0.136 | 0.14 / 0.05 | 0.058 / 0.045 | 0.25 / 1.11 / 3.1 | 4.2 | 494 | 10 |
| n2-1 | Xeon 6973P | 0.46 / 0.61 | 0.035 / 0.151 | 0.45 / 0.15 | 0.055 / 0.056 | 0.05 / 1.97 / 3.9 | 3.7 | 244 | 12 |
| n2-2 | EPYC 7763 | 0.69 / 0.75 | 0.135 / 0.133 | 0.12 / 0.06 | 0.053 / 0.046 | 0.30 / 1.87 / 4.9 | 6.6 | 660 | 13 |
| n2-3 | EPYC 7763 | 0.67 / 0.73 | 0.101 / 0.144 | 0.17 / 0.06 | 0.053 / 0.044 | 0.10 / 1.88 / 6.3 | 5.6 | 659 | 15 |
| n2-4 | EPYC 9V74 | 0.70 / 0.76 | 0.109 / 0.128 | 0.14 / 0.05 | 0.052 / 0.044 | 0.11 / 1.83 / 5.2 | 5.7 | 621 | 10 |
| rehearsal-1 | EPYC 7763 | 0.74 / 0.74 | 0.134 / 0.147 | 0.08 / 0.06 | 0.052 / 0.044 | 0.44 / 3.08 / 11.7 | 10.6 | 726 | 12 |
| rehearsal-2 | EPYC 7763 | 0.64 / 0.72 | 0.101 / 0.149 | 0.21 / 0.07 | 0.050 / 0.048 | 0.08 / 3.32 / 11.7 | 6.3 | 664 | 9 |
| rehearsal-3 | EPYC 9V74 | 0.67 / 0.68 | 0.123 / 0.101 | 0.17 / 0.16 | 0.044 / 0.036 | 0.08 / 2.51 / 11.6 | 6.8 | 454 | 20 |
| rehearsal-4 | Xeon 8370C | 0.61 / 0.69 | 0.130 / 0.139 | 0.21 / 0.10 | 0.053 / 0.047 | 0.12 / 3.14 / 15.5 | 7.2 | 474 | 18 |

**Worker capacity: lock time goes up, but workers do not wait more.**

- **Result wait.** The share of worker capacity lost waiting for ES to accept
  a finished group is the same in every variant: medians of 0.12-0.13 in the
  burst and 0.13-0.14 in the drain.
  - On EPYC 7763 it is 0.10-0.13 for controls, 0.12-0.13 for N=1 and
    0.10-0.14 for N=2.
  - Flushes are shorter and more frequent, so the expected remaining time when
    a worker lands on a running flush goes down. That offsets the higher lock
    duty.
- **Redispatch.** The time a worker waits for work because the queue is empty
  drops with max age: median 0.22 (control) to 0.14 (N=1) in the burst, and
  0.10 to 0.04 in the drain. Pipeline stages are enqueued sooner.
- **Idle gaps.** The longest idle gaps between groups in the burst shrink from
  10-19 s (control) to 3-6 s (N=1).
- **Busy share in the drain.** Higher with max age: medians of 0.67 (control),
  0.77 (N=1) and 0.74 (N=2). Per-op cost in the drain is the same on the same
  CPU.
- **What the drain depends on.** Mainly on how much work is left at the stop:
  worker-seconds busy after the stop. That is 634-726 on EPYC 7763 for every
  variant, except n1-4 with 494. The variant changes that number little.
- **Tail.** The tail after the workers stop being saturated is slightly
  shorter with N=1: 6-14 s against 9-20 s.
- **Group size.** Groups are smaller with max age: 4-5 ops in the burst for
  N=1, against 6-11 for the control-equivalent runs, because the queue is
  shorter.
- **Delivery.** Delivery time (job-group build + RPC) rises slightly, 0.056-0.062
  against 0.044-0.057 of capacity in the burst. That is too small to show in
  the drain.

### Q2 per run: host signals

| run | CPU | stack CPU % of 400, mean steady / burst / drain | samples > 360 % burst / drain | ES CPU cores steady / burst | PG CPU % steady / burst | ES RPC echo p99 burst / drain, max ms | monitor gaps > 4 s | max silence across all service logs, burst+drain s |
|---|---|---|---|---|---|---|---|---|
| control-1 | Xeon 8370C | 144 / 251 / 238 | 0.00 / 0.08 | 0.17 / 0.30 | 12 / 19 | 75 / 120, 138 | 0 | 0.44 |
| control-2 | EPYC 7763 | 136 / 220 / 250 | 0.00 / 0.00 | 0.15 / 0.27 | 12 / 19 | 89 / 64, 184 | 0 | 0.49 |
| control-3 | Xeon 6973P | 81 / 163 / 148 | 0.00 / 0.00 | 0.11 / 0.23 | 8 / 15 | 55 / 87, 108 | 0 | 0.41 |
| control-4 | EPYC 9V74 | 122 / 198 / 215 | 0.00 / 0.00 | 0.13 / 0.21 | 10 / 14 | 76 / 106, 130 | 0 | 0.48 |
| n1-1 | Xeon 8370C | 130 / 214 / 249 | 0.00 / 0.07 | 0.20 / 0.31 | 13 / 19 | 49 / 70, 99 | 0 | 0.49 |
| n1-2 | EPYC 7763 | 142 / 242 / 244 | 0.00 / 0.00 | 0.19 / 0.31 | 13 / 19 | 56 / 40, 106 | 0 | 0.44 |
| n1-3 | EPYC 7763 | 154 / 238 / 249 | 0.00 / 0.06 | 0.21 / 0.32 | 14 / 19 | 86 / 70, 101 | 0 | 0.37 |
| n1-4 | EPYC 7763 | 147 / 214 / 246 | 0.00 / 0.00 | 0.20 / 0.31 | 14 / 19 | 54 / 122, 144 | 0 | 0.48 |
| n2-1 | Xeon 6973P | 96 / 177 / 220 | 0.00 / 0.11 | 0.14 / 0.22 | 10 / 15 | 29 / 39, 51 | 0 | 0.65 |
| n2-2 | EPYC 7763 | 155 / 237 / 245 | 0.00 / 0.00 | 0.18 / 0.29 | 14 / 18 | 48 / 83, 167 | 0 | 0.42 |
| n2-3 | EPYC 7763 | 139 / 226 / 235 | 0.03 / 0.00 | 0.18 / 0.28 | 13 / 21 | 55 / 51, 80 | 0 | 0.38 |
| n2-4 | EPYC 9V74 | 162 / 218 / 230 | 0.00 / 0.00 | 0.20 / 0.27 | 14 / 17 | 39 / 76, 155 | 0 | 0.61 |
| rehearsal-1 | EPYC 7763 | 127 / 251 / 259 | 0.03 / 0.00 | 0.16 / 0.29 | 12 / 20 | 57 / 114, 122 | 0 | 0.50 |
| rehearsal-2 | EPYC 7763 | 147 / 226 / 225 | 0.03 / 0.00 | 0.15 / 0.25 | 13 / 19 | 41 / 65, 85 | 1 | 0.41 |
| rehearsal-3 | EPYC 9V74 | 119 / 199 / 197 | 0.00 / 0.00 | 0.12 / 0.22 | 9 / 16 | 29 / 73, 123 | 0 | 0.52 |
| rehearsal-4 | Xeon 8370C | 103 / 209 / 217 | 0.00 / 0.00 | 0.14 / 0.26 | 9 / 16 | 42 / 58, 198 | 0 | 0.49 |

**No host pauses or saturation.**

- The largest silence across all service logs during burst and drain is
  0.37-0.65 s in every run.
- Monitor samples are never more than 4 s apart, except one gap in rehearsal-2.
- ES RPC echo never goes over 200 ms.
- Stack CPU averages 160-260 % of 400 in the burst. At most 3 % of the burst
  samples and 11 % of the drain samples are over 360 %.
- Steal is not recorded. The only sign of a slow or noisy instance is the
  per-job worker time: n2-4 is the one outlier within its CPU model.
- Cost of max age:
  - ES CPU in the steady phase: +0.05-0.07 cores (0.11-0.17 to 0.19-0.21 for
    N=1).
  - PostgreSQL: +1-2 points of one core.
  - Negligible.

### Q2 per variant medians (mechanism)

| variant | flushes burst | results/flush burst | ES lock duty burst / drain | result-wait frac burst / drain | redispatch frac burst / drain | busy frac drain | idle gap p95 / max burst s | ES CPU cores steady |
|---|---|---|---|---|---|---|---|---|
| control | 66 | 103 | 0.35 / 0.58 | 0.119 / 0.138 | 0.22 / 0.10 | 0.67 | 3.29 / 14.5 | 0.14 |
| n1 | 141 | 38 | 0.47 / 0.68 | 0.127 / 0.133 | 0.14 / 0.04 | 0.77 | 1.17 / 4.0 | 0.20 |
| n2 | 84 | 66 | 0.39 / 0.61 | 0.105 / 0.138 | 0.16 / 0.06 | 0.74 | 1.87 / 5.0 | 0.18 |
| rehearsal | 60 | 104 | 0.32 / 0.62 | 0.126 / 0.143 | 0.19 / 0.09 | 0.70 | 3.11 / 11.7 | 0.15 |

## Q3. Recommendation

**Use N = 1 s, which is what the branch has** (`MAX_RESULT_AGE_SECONDS = 1`).

**Trade-off in the harness:**

| | N=1 | N=2 |
|---|---|---|
| steady p50 | -15 s (20.5 to 5.4 s) | -12 s (to 8.9 s) |
| steady p95 | -40 s (52 to 14 s) | -30 s (to 24.5 s) |
| first half of the burst, p50 | ~21-25 s to ~10 s | about half as large as N=1 |
| drain after the stop | -0.1 s (CI ±21 s) | +4 s (CI -17 to +25 s) |

- N=1 is 3.5 s better than N=2 at steady p50 and 10 s better at p95.
- N=2 is not better than N=1 on any measured axis.
- The drain after the stop does not change measurably for either variant.
- The cost is about 4x more flush transactions (~0.5/s in the steady phase),
  +0.05 ES cores, and +17-34 % ES lock duty in burst and drain. The lock duty
  does not turn into more worker waiting.

**Translating to a real contest** (OMI 2025 profile: 37-70 evaluations per
submission, TL 1-10 s, 2-4 busy workers mid-contest, ~11 busy workers and
p95 128 s at the end of d2):

- **Steady phase, i.e. contestant feedback.**
  - The gain is about the number of flush-gated stages times the drop in the
    gap between flushes.
  - In production the stages are the same (two-phase plus `depends_on`), and
    each one waits at most ~N s plus the flush for its results to be written.
  - Without max age, the wait is "100 results or 2 s of quiet". With long
    testcases and 2-4 busy workers, 100 results take tens of seconds, and a
    continuous trickle prevents the quiet rule from firing.
  - So the absolute saving per submission should be at least a few seconds,
    up to the ~15 s seen here.
  - Evaluation itself takes longer in production, so the relative gain will be
    smaller than 75 %.
- **Drain, i.e. publishing results.**
  - Production's end-of-contest backlog is bound by worker capacity.
  - Testcases take ~1-10 s there against ~0.14 s here, so ES flush and lock
    time is a much smaller share of each worker cycle. Any cost of the max
    age would be even smaller than in the harness, where none could be
    measured.
  - Expect no change in time-to-publish.

**Do not add a "max age only while the buffer is small or the rate is low"
variant.** There is no measured cost in the burst or drain for it to avoid.
It would add a tunable and a code path without evidence.

**Do issue #6's second item next, as a separate change with its own load
test: release the worker before taking `post_finish_lock`.**

- In every variant, workers lose ~12-14 % of their capacity in burst and drain
  waiting for ES to accept their results (p95 up to 1.8 s per group in the
  burst, max 2.6-5.7 s), plus ~5 % in delivery.
- On the slow runners, workers are 0.70-0.79 busy in the drain. Turning most
  of the result wait into work would shorten the saturated part of the drain
  by at most ~10-15 %, about 15-20 s on EPYC 7763 (~140 s). That is an
  estimate, not a measurement.
- This is the lever for time-to-publish; the max age is not.
- It moves `release_worker` and the to-ignore handling out of the lock, so it
  needs a careful concurrency review against invalidation and
  `check_timeouts`/`check_connections`.
- Smaller items in the same area:
  - The job group is built in a thread with a DB read (delivery ~5 %).
  - ES runs on the default executor (8 threads on 4 vCPUs). While a flush
    holds the lock, up to 6 `_action_finished_sync` threads sit blocked, which
    leaves little room for `_build_job_group_dict`.

**Confidence:**

- **High** that the steady-phase gain is real and about this size. It is
  uniform over 4 runs per variant and 4 CPU models, more than 20 residual SDs.
- **Moderate to high** that N=1 does not worsen the drain or end-burst p95.
  Same-CPU comparisons and the speed regression agree, and the mechanism
  agrees: no more result wait, less empty-queue time, higher busy share in the
  drain. But with 4 runs per variant, effects up to about ±20 s cannot be
  ruled out.
- **Low to moderate** on the size of the gain from the second item; that one
  is extrapolated.

## Caveats

- Runner assignment is not controlled, and the CPU model changes drain by 2x
  (70 s on 6973P against 150-165 s on 7763). Future A/B runs should compare
  within the same CPU model, or report the per-job worker time next to the
  drain. Running both variants on one runner, as the fork/upstream pairs do,
  would remove this noise.
- The runner-speed regressor (steady-phase per-job time) is measured in the
  same run. It is not an effect of the variant: on the same CPU model, the
  per-job times of N=1 and N=2 match the controls.
- The rehearsal build is older than the driver fix that stops POSTs after the
  stop, but its runs show 0 rejected submissions. Its CWS code differs from
  control, which does not touch ES or the workers. Its steady numbers match
  control within 1-2 s.
- Synthetic testcases are cheap, so ES and lock overhead weighs more in the
  harness than in production. That makes the harness a pessimistic test of
  the max age, not an optimistic one.
