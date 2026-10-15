# Run ci-1-fork

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791394744.129602.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 253 | 0 | 0.013 | 0.084 | 0.197 | 0.304 |
| contest_page | steady | 98 | 0 | 0.124 | 0.810 | 1.656 | 2.847 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.009 | 0.035 | 0.168 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.011 | 0.014 | 0.057 |
| login | login_burst | 250 | 0 | 0.279 | 0.295 | 0.301 | 0.317 |
| notifications | login_burst | 508 | 0 | 0.010 | 0.014 | 0.024 | 0.035 |
| notifications | start_burst | 497 | 0 | 0.011 | 0.194 | 0.386 | 0.586 |
| notifications | steady | 9425 | 0 | 0.115 | 0.905 | 2.335 | 6.595 |
| notifications | end_burst | 2402 | 0 | 0.365 | 7.609 | 12.263 | 22.892 |
| notifications | drain | 9727 | 0 | 0.049 | 1.280 | 11.083 | 21.441 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.007 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.003 | 0.007 | 0.008 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.014 | 0.026 | 0.105 |
| rws_scores | end_burst | 149 | 0 | 0.004 | 0.017 | 0.054 | 0.119 |
| rws_scores | drain | 599 | 0 | 0.003 | 0.013 | 0.030 | 0.114 |
| statement | start_burst | 425 | 0 | 0.010 | 0.091 | 0.432 | 0.645 |
| status_poll | start_burst | 102 | 0 | 0.012 | 0.131 | 0.330 | 0.500 |
| status_poll | steady | 6153 | 0 | 0.101 | 0.837 | 1.874 | 6.466 |
| status_poll | end_burst | 3658 | 0 | 0.455 | 9.061 | 19.446 | 22.966 |
| status_poll | drain | 2640 | 0 | 0.142 | 8.263 | 14.895 | 21.176 |
| submission_details | start_burst | 7 | 0 | 0.075 | 0.194 | 0.198 | 0.200 |
| submission_details | steady | 273 | 0 | 0.193 | 0.772 | 1.143 | 1.979 |
| submission_details | end_burst | 79 | 0 | 0.479 | 3.240 | 8.741 | 10.576 |
| submission_details | drain | 196 | 0 | 0.190 | 1.832 | 12.545 | 13.755 |
| submissions_list | start_burst | 3 | 0 | 0.080 | 0.106 | 0.109 | 0.109 |
| submissions_list | steady | 84 | 0 | 0.220 | 1.408 | 2.831 | 3.154 |
| submissions_page | start_burst | 27 | 0 | 0.052 | 0.345 | 0.502 | 0.553 |
| submissions_page | steady | 601 | 0 | 0.207 | 1.024 | 1.961 | 2.717 |
| submissions_page | end_burst | 412 | 0 | 0.512 | 9.136 | 16.486 | 17.317 |
| submissions_page | drain | 37 | 0 | 12.971 | 16.284 | 18.574 | 19.586 |
| submit | start_burst | 27 | 0 | 0.030 | 0.294 | 0.364 | 0.383 |
| submit | steady | 601 | 0 | 0.141 | 0.926 | 1.615 | 3.993 |
| submit | end_burst | 449 | 0 | 0.611 | 10.953 | 15.691 | 22.456 |
| submit | drain | 17 | 0 | 11.563 | 17.902 | 19.269 | 19.611 |
| task_description | start_burst | 629 | 0 | 0.011 | 0.085 | 0.374 | 0.463 |
| task_description | steady | 188 | 0 | 0.099 | 1.088 | 2.055 | 5.942 |
| task_description | end_burst | 127 | 0 | 0.411 | 13.379 | 22.270 | 22.999 |

All phases: login p50 0.279 s, p95 0.295 s; submit p50 0.221 s, p95 9.726 s; submit in end_burst p95 10.953 s.

Peak CWS request rate: 88 req/s (1 s buckets); mean 14.5 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1094; rejected by CWS: 17; DB rows: 1077.
  - rejected a077 suma status=302 location=../../../loada
  - rejected a120 umbral status=302 location=../../../loada
  - rejected b016 sumab status=302 location=../../../loadb
  - rejected a151 umbral status=302 location=../../../loada
  - rejected b043 umbralb status=302 location=../../../loadb
  - rejected a160 suma status=302 location=../../../loada
  - rejected a153 libre status=302 location=../../../loada
  - rejected a053 umbral status=302 location=../../../loada
  - rejected a090 suma status=302 location=../../../loada
  - rejected b047 mulb status=302 location=../../../loadb

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 27 | 13.3 | 34.5 | 40.6 | 16.6 | 48.8 | 62.9 |
| steady | 601 | 136.7 | 211.7 | 227.2 | 166.5 | 278.9 | 332.8 |
| end_burst | 449 | 464.4 | 746.0 | 784.9 | 551.9 | 966.6 | 1185.9 |

All: server submit->scored p50 184.6 s, p95 704.1 s, max 784.9 s.
Last submission scored 782 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'wa_overflow': 146, 'ac': 334, 'ce': 82, 'tle': 140, 'wa_zero': 52, 'ac_py': 100, 'wa_small': 106, 'ac_java': 42, 're': 92}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1077}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=491 p50 1.2 p95 2.0 max 2.3 s

## Internals (monitor, 2 s samples)

ES queue: max 8605 operations (max 361 distinct entries); workers busy mean 6.0, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:21/1.9 +1:286/6.4 +2:355/8.0 +3:492/8.0 +4:610/8.0 +5:681/8.0 +6:856/8.0 +7:978/8.0 +8:1065/8.0 +9:1200/8.0 +10:958/8.0 +11:1026/8.0 +12:1250/8.0 +13:1172/8.0 +14:1377/8.0 +15:1446/8.0 +16:1627/8.0 +17:1849/8.0 +18:1675/8.0 +19:1377/8.0 +20:1640/8.0 +21:1670/8.0 +22:2996/8.0 +23:3756/8.0 +24:5932/8.0 +25:8605/8.0 +26:8530/8.0 +27:7899/8.0 +28:7049/8.0 +29:6349/8.0 +30:5899/8.0 +31:5149/8.0 +32:4274/8.0 +33:3749/8.0 +34:2974/8.0 +35:1974/8.0 +36:1299/8.0 +37:449/7.6 +38:0/0.0 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0 +43:0/0.0 +44:0/0.0 +45:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 2.7 | 74.6 | 1220.2 | 2 | 1 | 0 |
| CWS0 | 5.5 | 2870.7 | 8612.0 | 238 | 74 | 3 |
| CWS1 | 4.8 | 2023.8 | 10434.3 | 192 | 49 | 1 |
| ES | 3.0 | 497.7 | 4771.1 | 22 | 6 | 0 |
| PS | 2.7 | 57.3 | 1973.1 | 2 | 1 | 0 |
| SS | 2.8 | 52.0 | 279.2 | 1 | 0 | 0 |
- monitor RPC error CWS0: TimeoutError('timed out') (x3)
- monitor RPC error CWS1: TimeoutError('timed out') (x1)

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 8.4 | 87 |
| ContestWebServer0 | 3 | 293.7 | 148 |
| ContestWebServer1 | 3 | 296.8 | 150 |
| EvaluationService0 | 8 | 178.1 | 98 |
| LogService0 | 0 | 60.1 | 84 |
| ProxyService0 | 3 | 19.1 | 92 |
| ScoringService0 | 2 | 40.6 | 95 |
| Worker0 | 1 | 105.0 | 87 |
| Worker1 | 1 | 102.6 | 94 |
| Worker2 | 1 | 103.3 | 87 |
| Worker3 | 2 | 103.2 | 88 |
| Worker4 | 1 | 100.3 | 89 |
| Worker5 | 1 | 97.3 | 89 |
| Worker6 | 1 | 99.6 | 88 |
| Worker7 | 1 | 101.2 | 88 |

PostgreSQL backends for cmsdb: max 27 (max_connections 100); max 'active' 3; max lock waits 1; longest idle-in-transaction 11.2 s.

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
- EvaluationService-0: sweeper found 0 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 2 missed operations in total
- cms: sweeper found 2 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 2
- x2 db-N  | N-N-N N:N:N UTC [N] LOG:  duration: N ms  execute <unnamed>: SELECT pg_advisory_xact_lock(CAST($N AS integer), i
Errors/lock waits: 1
- x1 [N] LOG:  process N still waiting for ExclusiveLock on advisory lock [N,N,N,N] after N.N ms

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 96% max 324% (100% = 1 core); last mem 1.139GiB
- cmsload-fork-db-1: CPU mean 9% max 71% (100% = 1 core); last mem 112.8MiB
- cmsload-fork-driver-1: CPU mean 3% max 11% (100% = 1 core); last mem 1.457MiB
- cmsload-fork-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 28.06MiB
