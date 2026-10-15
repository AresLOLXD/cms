# Run ci-2-fork

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791397634.078059.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 253 | 0 | 0.013 | 0.091 | 0.186 | 0.292 |
| contest_page | steady | 77 | 0 | 0.116 | 0.942 | 1.835 | 1.924 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.011 | 0.023 | 0.195 |
| home_logged | login_burst | 250 | 0 | 0.008 | 0.010 | 0.013 | 0.023 |
| login | login_burst | 250 | 0 | 0.280 | 0.297 | 0.307 | 0.329 |
| notifications | login_burst | 508 | 0 | 0.011 | 0.015 | 0.025 | 0.142 |
| notifications | start_burst | 500 | 0 | 0.011 | 0.128 | 0.215 | 0.384 |
| notifications | steady | 9418 | 0 | 0.112 | 1.005 | 2.164 | 4.126 |
| notifications | end_burst | 2413 | 0 | 0.470 | 8.738 | 21.359 | 27.889 |
| notifications | drain | 9703 | 0 | 0.055 | 1.206 | 17.308 | 29.959 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.003 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.012 | 0.022 | 0.024 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.015 | 0.034 | 0.121 |
| rws_scores | end_burst | 149 | 0 | 0.004 | 0.017 | 0.036 | 0.038 |
| rws_scores | drain | 608 | 0 | 0.003 | 0.014 | 0.023 | 0.077 |
| statement | start_burst | 450 | 0 | 0.010 | 0.115 | 0.275 | 0.478 |
| status_poll | start_burst | 147 | 0 | 0.018 | 0.175 | 0.338 | 0.439 |
| status_poll | steady | 5855 | 0 | 0.101 | 0.850 | 1.967 | 4.482 |
| status_poll | end_burst | 3470 | 0 | 0.573 | 9.281 | 17.405 | 28.133 |
| status_poll | drain | 2709 | 0 | 0.147 | 18.528 | 22.957 | 34.540 |
| submission_details | start_burst | 6 | 0 | 0.066 | 0.189 | 0.210 | 0.215 |
| submission_details | steady | 222 | 0 | 0.173 | 0.757 | 2.203 | 3.001 |
| submission_details | end_burst | 73 | 0 | 0.394 | 4.465 | 9.062 | 13.150 |
| submission_details | drain | 206 | 0 | 0.136 | 1.418 | 19.346 | 22.201 |
| submissions_list | start_burst | 3 | 0 | 0.154 | 0.277 | 0.288 | 0.291 |
| submissions_list | steady | 92 | 0 | 0.203 | 0.681 | 0.990 | 1.522 |
| submissions_page | start_burst | 33 | 0 | 0.049 | 0.203 | 0.287 | 0.306 |
| submissions_page | steady | 555 | 0 | 0.189 | 0.915 | 1.722 | 2.963 |
| submissions_page | end_burst | 386 | 0 | 0.868 | 9.540 | 15.651 | 27.185 |
| submissions_page | drain | 46 | 0 | 19.341 | 25.617 | 27.774 | 29.270 |
| submit | start_burst | 33 | 0 | 0.033 | 0.147 | 0.181 | 0.192 |
| submit | steady | 555 | 0 | 0.137 | 0.767 | 2.013 | 2.822 |
| submit | end_burst | 432 | 0 | 1.033 | 15.573 | 22.110 | 27.217 |
| submit | drain | 22 | 0 | 19.343 | 23.516 | 27.858 | 29.001 |
| task_description | start_burst | 650 | 0 | 0.012 | 0.098 | 0.210 | 0.358 |
| task_description | steady | 165 | 0 | 0.115 | 0.841 | 1.672 | 3.531 |
| task_description | end_burst | 131 | 0 | 1.357 | 15.767 | 27.456 | 27.767 |

All phases: login p50 0.280 s, p95 0.297 s; submit p50 0.239 s, p95 14.241 s; submit in end_burst p95 15.573 s.

Peak CWS request rate: 95 req/s (1 s buckets); mean 14.3 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1042; rejected by CWS: 22; DB rows: 1020.
  - rejected a155 libre status=302 location=../../../loada
  - rejected b004 sumab status=302 location=../../../loadb
  - rejected a117 libre status=302 location=../../../loada
  - rejected b003 umbralb status=302 location=../../../loadb
  - rejected b010 mulb status=302 location=../../../loadb
  - rejected b050 mulb status=302 location=../../../loadb
  - rejected b022 mulb status=302 location=../../../loadb
  - rejected b056 mulb status=302 location=../../../loadb
  - rejected a099 suma status=302 location=../../../loada
  - rejected a148 umbral status=302 location=../../../loada

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 33 | 27.0 | 45.2 | 47.0 | 37.9 | 61.7 | 64.0 |
| steady | 555 | 186.3 | 241.6 | 260.7 | 225.4 | 333.7 | 390.1 |
| end_burst | 432 | 481.7 | 830.2 | 874.4 | 593.8 | 1069.1 | 1209.0 |

All: server submit->scored p50 228.0 s, p95 775.9 s, max 874.4 s.
Last submission scored 871 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ac': 286, 'wa_zero': 58, 'ac_py': 107, 'tle': 143, 'ce': 79, 'wa_small': 109, 'wa_overflow': 139, 're': 86, 'ac_java': 35}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1020}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=490 p50 1.0 p95 2.0 max 2.3 s

## Internals (monitor, 2 s samples)

ES queue: max 8106 operations (max 343 distinct entries); workers busy mean 6.2, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:155/2.0 +1:235/8.0 +2:321/8.0 +3:561/8.0 +4:687/8.0 +5:841/8.0 +6:820/8.0 +7:1039/8.0 +8:1318/8.0 +9:1376/8.0 +10:1485/8.0 +11:1335/8.0 +12:1554/8.0 +13:1928/8.0 +14:2006/8.0 +15:1960/8.0 +16:1753/8.0 +17:1622/8.0 +18:1506/8.0 +19:1513/8.0 +20:1810/8.0 +21:2038/8.0 +22:3049/8.0 +23:4700/8.0 +24:6975/8.0 +25:7942/8.0 +26:8106/8.0 +27:7681/8.0 +28:7056/8.0 +29:6456/8.0 +30:5731/8.0 +31:4981/8.0 +32:4306/8.0 +33:3856/8.0 +34:3156/8.0 +35:2406/8.0 +36:1881/8.0 +37:1381/8.0 +38:756/8.0 +39:138/3.4 +40:0/0.0 +41:0/0.0 +42:0/0.0 +43:0/0.0 +44:0/0.0 +45:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 2.8 | 50.2 | 218.0 | 0 | 0 | 0 |
| CWS0 | 6.6 | 2518.2 | 8702.8 | 235 | 56 | 4 |
| CWS1 | 5.5 | 2144.8 | 9956.6 | 186 | 45 | 4 |
| ES | 3.5 | 731.1 | 3561.6 | 29 | 7 | 0 |
| PS | 2.9 | 55.2 | 209.9 | 0 | 0 | 0 |
| SS | 2.9 | 97.9 | 2279.8 | 6 | 3 | 0 |
- monitor RPC error CWS1: TimeoutError('timed out') (x4)
- monitor RPC error CWS0: TimeoutError('timed out') (x4)

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 9.0 | 87 |
| ContestWebServer0 | 3 | 301.6 | 154 |
| ContestWebServer1 | 3 | 303.6 | 152 |
| EvaluationService0 | 5 | 170.8 | 97 |
| LogService0 | 0 | 59.5 | 84 |
| ProxyService0 | 3 | 18.2 | 93 |
| ScoringService0 | 1 | 38.9 | 94 |
| Worker0 | 1 | 100.1 | 87 |
| Worker1 | 1 | 107.5 | 87 |
| Worker2 | 1 | 109.6 | 89 |
| Worker3 | 1 | 105.8 | 92 |
| Worker4 | 1 | 108.2 | 94 |
| Worker5 | 1 | 109.7 | 89 |
| Worker6 | 1 | 115.8 | 94 |
| Worker7 | 2 | 110.4 | 87 |

PostgreSQL backends for cmsdb: max 26 (max_connections 100); max 'active' 3; max lock waits 1; longest idle-in-transaction 69.9 s.

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
- EvaluationService-0: sweeper found 7 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 7 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 6
- x6 db-N  | N-N-N N:N:N UTC [N] LOG:  duration: N ms  execute <unnamed>: SELECT pg_advisory_xact_lock(CAST($N AS integer), i
Errors/lock waits: 6
- x6 [N] LOG:  process N still waiting for ExclusiveLock on advisory lock [N,N,N,N] after N.N ms

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 101% max 324% (100% = 1 core); last mem 1.147GiB
- cmsload-fork-db-1: CPU mean 9% max 27% (100% = 1 core); last mem 104.5MiB
- cmsload-fork-driver-1: CPU mean 3% max 11% (100% = 1 core); last mem 1.352MiB
- cmsload-fork-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 33.07MiB
