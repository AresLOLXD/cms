# Run ci-1-upstream

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791397750.506657.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 253 | 0 | 0.015 | 0.867 | 1.221 | 1.891 |
| contest_page | steady | 81 | 0 | 0.167 | 2.386 | 4.505 | 6.768 |
| home_anon | login_burst | 250 | 0 | 0.008 | 0.489 | 0.892 | 1.071 |
| home_logged | login_burst | 250 | 0 | 0.011 | 0.490 | 1.350 | 1.753 |
| login | login_burst | 250 | 0 | 0.284 | 1.046 | 1.409 | 1.843 |
| notifications | login_burst | 506 | 0 | 0.012 | 0.621 | 1.357 | 2.345 |
| notifications | start_burst | 494 | 0 | 0.040 | 0.472 | 0.987 | 1.803 |
| notifications | steady | 9279 | 0 | 0.238 | 2.570 | 4.877 | 11.191 |
| notifications | end_burst | 2437 | 0 | 0.713 | 5.670 | 11.114 | 20.009 |
| notifications | drain | 9804 | 0 | 0.128 | 2.056 | 3.893 | 6.700 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.005 | 0.008 | 0.008 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.013 | 0.034 | 0.099 |
| rws_scores | end_burst | 149 | 0 | 0.003 | 0.017 | 0.032 | 0.041 |
| rws_scores | drain | 609 | 0 | 0.003 | 0.016 | 0.029 | 0.072 |
| statement | start_burst | 429 | 0 | 0.029 | 0.680 | 1.531 | 1.828 |
| status_poll | start_burst | 114 | 0 | 0.058 | 0.639 | 0.993 | 1.456 |
| status_poll | steady | 5884 | 0 | 0.170 | 2.027 | 3.939 | 9.658 |
| status_poll | end_burst | 3654 | 0 | 0.760 | 5.109 | 9.391 | 25.331 |
| status_poll | drain | 3380 | 0 | 0.136 | 3.085 | 5.110 | 8.203 |
| submission_details | start_burst | 7 | 0 | 0.110 | 0.438 | 0.497 | 0.512 |
| submission_details | steady | 259 | 0 | 0.240 | 2.313 | 3.388 | 7.540 |
| submission_details | end_burst | 53 | 0 | 0.849 | 3.509 | 5.169 | 5.882 |
| submission_details | drain | 221 | 0 | 0.149 | 1.052 | 1.954 | 2.717 |
| submissions_list | start_burst | 3 | 0 | 0.329 | 0.583 | 0.606 | 0.612 |
| submissions_list | steady | 101 | 0 | 0.315 | 2.082 | 4.522 | 6.603 |
| submissions_page | start_burst | 25 | 0 | 0.177 | 0.324 | 0.430 | 0.458 |
| submissions_page | steady | 607 | 0 | 0.295 | 2.236 | 3.816 | 9.337 |
| submissions_page | end_burst | 449 | 0 | 0.954 | 5.161 | 8.372 | 15.061 |
| submissions_page | drain | 26 | 0 | 3.698 | 6.562 | 8.351 | 8.829 |
| submit | start_burst | 25 | 0 | 0.111 | 0.754 | 1.090 | 1.186 |
| submit | steady | 607 | 0 | 0.281 | 3.869 | 6.976 | 11.013 |
| submit | end_burst | 475 | 0 | 1.776 | 10.995 | 17.382 | 27.015 |
| submit | drain | 1 | 0 | 0.196 | 0.196 | 0.196 | 0.196 |
| task_description | start_burst | 633 | 0 | 0.016 | 0.665 | 1.098 | 1.478 |
| task_description | steady | 191 | 0 | 0.166 | 2.130 | 4.866 | 7.328 |
| task_description | end_burst | 149 | 0 | 0.795 | 3.594 | 9.687 | 16.646 |

All phases: login p50 0.284 s, p95 1.046 s; submit p50 0.493 s, p95 7.078 s; submit in end_burst p95 10.995 s.

Peak CWS request rate: 87 req/s (1 s buckets); mean 14.6 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1108; rejected by CWS: 1; DB rows: 1107.
  - rejected a166 suma status=302 location=../../../loada

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 25 | 12.5 | 21.4 | 21.6 | 16.8 | 24.9 | 26.4 |
| steady | 607 | 136.9 | 167.2 | 205.4 | 159.0 | 219.6 | 315.2 |
| end_burst | 475 | 616.1 | 948.9 | 991.5 | 718.5 | 1202.2 | 1221.3 |

All: server submit->scored p50 152.4 s, p95 900.1 s, max 991.5 s.
Last submission scored 989 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 1
  - b054 sumab ac expected 100.0 got 60.0 (id 151, evaluations 24)
Mix: {'ac': 328, 'ce': 68, 'wa_small': 118, 'tle': 159, 'wa_overflow': 143, 'wa_zero': 49, 're': 102, 'ac_py': 109, 'ac_java': 32}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1107}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=527 p50 1.1 p95 2.0 max 2.7 s

## Internals (monitor, 2 s samples)

ES queue: max 9954 operations (max 412 distinct entries); workers busy mean 6.6, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:87/3.9 +1:153/6.5 +2:191/7.6 +3:261/8.0 +4:351/8.0 +5:797/8.0 +6:920/8.0 +7:1036/8.0 +8:1030/7.9 +9:818/8.0 +10:738/8.0 +11:1057/8.0 +12:1062/7.8 +13:1133/8.0 +14:1091/7.9 +15:1202/7.9 +16:1105/8.0 +17:1140/7.9 +18:1074/7.9 +19:971/8.0 +20:1562/8.0 +21:1751/7.9 +22:2833/7.9 +23:3762/8.0 +24:6411/8.0 +25:9954/8.0 +26:9904/8.0 +27:9429/8.0 +28:8929/8.0 +29:8404/8.0 +30:7829/8.0 +31:7254/8.0 +32:6504/8.0 +33:5879/8.0 +34:5229/7.9 +35:4554/8.0 +36:3979/7.9 +37:2979/8.0 +38:2379/8.0 +39:1629/8.0 +40:979/8.0 +41:136/3.1 +42:0/0.0 +43:0/0.0 +44:0/0.0 +45:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 3.3 | 60.5 | 977.6 | 1 | 0 | 0 |
| CWS0 | 8.9 | 557.5 | 1358.5 | 90 | 4 | 0 |
| CWS1 | 8.2 | 545.7 | 1850.0 | 77 | 2 | 0 |
| ES | 4.9 | 585.5 | 6290.0 | 33 | 9 | 0 |
| PS | 3.4 | 83.7 | 2608.9 | 4 | 2 | 0 |
| SS | 3.6 | 154.7 | 631.3 | 6 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 7.3 | 73 |
| ContestWebServer0 | 20 | 355.6 | 129 |
| ContestWebServer1 | 17 | 353.2 | 129 |
| EvaluationService0 | 9 | 180.9 | 79 |
| LogService0 | 0 | 86.9 | 66 |
| ProxyService0 | 4 | 19.6 | 78 |
| ScoringService0 | 2 | 39.4 | 74 |
| Worker0 | 1 | 100.0 | 74 |
| Worker1 | 1 | 98.4 | 72 |
| Worker2 | 1 | 100.3 | 72 |
| Worker3 | 1 | 99.8 | 72 |
| Worker4 | 1 | 96.2 | 73 |
| Worker5 | 2 | 98.7 | 72 |
| Worker6 | 1 | 98.6 | 82 |
| Worker7 | 1 | 96.5 | 81 |

PostgreSQL backends for cmsdb: max 54 (max_connections 100); max 'active' 16; max lock waits 0; longest idle-in-transaction 18.1 s.

## Service logs

Log files: 16

| service | WARNING | ERROR | CRITICAL |
|---|---|---|---|
| AdminWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-1 | 0 | 0 | 0 |
| EvaluationService-0 | 0 | 0 | 0 |
| LogService-0 | 0 | 0 | 0 |
| ProxyService-0 | 0 | 0 | 0 |
| ScoringService-0 | 0 | 0 | 0 |
| Worker-0 | 0 | 0 | 0 |
| Worker-1 | 0 | 0 | 0 |
| Worker-2 | 0 | 0 | 0 |
| Worker-3 | 0 | 0 | 0 |
| Worker-4 | 0 | 0 | 0 |
| Worker-5 | 0 | 0 | 0 |
| Worker-6 | 0 | 0 | 0 |
| Worker-7 | 0 | 0 | 0 |
| cms | 0 | 0 | 0 |

Pattern hits:
- EvaluationService-0: missed operation(s) x26
- ProxyService-0: missed operation(s) x9
- ScoringService-0: missed operation(s) x9
- cms: missed operation(s) x44
- EvaluationService-0: sweeper found 1 missed operations in total
- ProxyService-0: sweeper found 2 missed operations in total
- ScoringService-0: sweeper found 1 missed operations in total
- cms: sweeper found 4 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 99% max 294% (100% = 1 core); last mem 982.9MiB
- cmsload-upstream-db-1: CPU mean 13% max 93% (100% = 1 core); last mem 119.6MiB
- cmsload-upstream-driver-1: CPU mean 3% max 13% (100% = 1 core); last mem 1.371MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 40.55MiB
