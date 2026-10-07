# Run ci-4-fork

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791397629.423434.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 251 | 0 | 0.012 | 0.021 | 0.044 | 0.264 |
| contest_page | steady | 93 | 0 | 0.124 | 0.827 | 1.198 | 1.376 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.008 | 0.018 | 0.180 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.009 | 0.013 | 0.014 |
| login | login_burst | 250 | 0 | 0.313 | 0.334 | 0.349 | 0.364 |
| notifications | login_burst | 508 | 0 | 0.010 | 0.014 | 0.023 | 0.071 |
| notifications | start_burst | 499 | 0 | 0.032 | 0.872 | 1.224 | 1.504 |
| notifications | steady | 9431 | 0 | 0.094 | 0.697 | 1.159 | 4.146 |
| notifications | end_burst | 2385 | 0 | 0.595 | 8.284 | 18.737 | 32.513 |
| notifications | drain | 8601 | 0 | 0.039 | 1.307 | 16.742 | 31.279 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.002 | 0.009 | 0.017 | 0.020 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.013 | 0.026 | 0.135 |
| rws_scores | end_burst | 149 | 0 | 0.004 | 0.016 | 0.040 | 0.086 |
| rws_scores | drain | 535 | 0 | 0.003 | 0.012 | 0.026 | 0.054 |
| statement | start_burst | 442 | 0 | 0.009 | 0.042 | 0.196 | 0.396 |
| status_poll | start_burst | 114 | 0 | 0.148 | 1.314 | 1.682 | 1.756 |
| status_poll | steady | 5800 | 0 | 0.086 | 0.659 | 1.237 | 4.063 |
| status_poll | end_burst | 3504 | 0 | 0.930 | 9.278 | 15.526 | 32.780 |
| status_poll | drain | 2975 | 0 | 0.095 | 17.568 | 23.875 | 31.527 |
| submission_details | start_burst | 2 | 0 | 0.655 | 1.185 | 1.232 | 1.244 |
| submission_details | steady | 268 | 0 | 0.163 | 0.741 | 1.221 | 1.835 |
| submission_details | end_burst | 92 | 0 | 0.608 | 5.570 | 7.743 | 9.921 |
| submission_details | drain | 199 | 0 | 0.111 | 13.425 | 21.600 | 23.519 |
| submissions_list | start_burst | 7 | 0 | 0.573 | 1.172 | 1.173 | 1.173 |
| submissions_list | steady | 87 | 0 | 0.183 | 0.661 | 1.063 | 1.249 |
| submissions_page | start_burst | 25 | 0 | 0.155 | 1.251 | 1.620 | 1.726 |
| submissions_page | steady | 597 | 0 | 0.160 | 0.721 | 1.224 | 1.690 |
| submissions_page | end_burst | 453 | 0 | 1.283 | 9.997 | 16.802 | 20.376 |
| submissions_page | drain | 39 | 0 | 18.494 | 26.303 | 29.287 | 30.929 |
| submit | start_burst | 27 | 0 | 0.095 | 1.094 | 1.472 | 1.562 |
| submit | steady | 595 | 0 | 0.112 | 0.609 | 0.965 | 1.773 |
| submit | end_burst | 492 | 0 | 1.598 | 11.527 | 14.961 | 24.017 |
| submit | drain | 15 | 0 | 15.713 | 26.018 | 26.182 | 26.223 |
| task_description | start_burst | 648 | 0 | 0.011 | 0.052 | 0.285 | 0.982 |
| task_description | steady | 179 | 0 | 0.091 | 0.586 | 1.108 | 1.429 |
| task_description | end_burst | 150 | 0 | 1.391 | 13.015 | 18.984 | 20.230 |

All phases: login p50 0.313 s, p95 0.334 s; submit p50 0.220 s, p95 9.294 s; submit in end_burst p95 11.527 s.

Peak CWS request rate: 104 req/s (1 s buckets); mean 14.6 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1129; rejected by CWS: 15; DB rows: 1114.
  - rejected a134 libre status=302 location=../../../loada
  - rejected a066 cadena status=302 location=../../../loada
  - rejected a055 cadena status=302 location=../../../loada
  - rejected a100 umbral status=302 location=../../../loada
  - rejected a002 libre status=302 location=../../../loada
  - rejected a080 umbral status=302 location=../../../loada
  - rejected a036 libre status=302 location=../../../loada
  - rejected b054 umbralb status=302 location=../../../loadb
  - rejected a156 suma status=302 location=../../../loada
  - rejected a081 cadena status=302 location=../../../loada

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 27 | 28.3 | 39.8 | 43.0 | 36.4 | 52.4 | 56.8 |
| steady | 595 | 122.5 | 143.6 | 160.1 | 144.6 | 195.1 | 230.9 |
| end_burst | 492 | 399.1 | 703.5 | 733.0 | 477.7 | 848.8 | 1058.2 |

All: server submit->scored p50 135.1 s, p95 669.6 s, max 733.0 s.
Last submission scored 730 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 75, 'ac': 339, 're': 94, 'wa_overflow': 153, 'tle': 150, 'wa_zero': 56, 'wa_small': 120, 'ac_py': 113, 'ac_java': 29}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1114}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=548 p50 1.1 p95 2.0 max 2.3 s

## Internals (monitor, 2 s samples)

ES queue: max 8006 operations (max 340 distinct entries); workers busy mean 6.2, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:55/3.7 +1:216/7.9 +2:214/8.0 +3:150/8.0 +4:348/8.0 +5:597/8.0 +6:553/8.0 +7:845/8.0 +8:1013/8.0 +9:1042/8.0 +10:961/8.0 +11:1009/8.0 +12:880/8.0 +13:1163/8.0 +14:1034/8.0 +15:1029/8.0 +16:788/8.0 +17:874/8.0 +18:1068/8.0 +19:1054/8.0 +20:1358/8.0 +21:1772/8.0 +22:2971/8.0 +23:4224/8.0 +24:6090/8.0 +25:8006/8.0 +26:7977/8.0 +27:7452/8.0 +28:6702/8.0 +29:5977/8.0 +30:5427/8.0 +31:4752/8.0 +32:4052/8.0 +33:3277/8.0 +34:2352/8.0 +35:1727/8.0 +36:927/8.0 +37:0/0.3 +38:0/0.0 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0 +43:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 2.5 | 52.8 | 219.1 | 0 | 0 | 0 |
| CWS0 | 4.9 | 2319.2 | 5614.0 | 189 | 57 | 5 |
| CWS1 | 4.3 | 1835.3 | 9884.4 | 160 | 44 | 6 |
| ES | 2.8 | 324.8 | 3717.9 | 21 | 4 | 0 |
| PS | 2.5 | 67.6 | 1109.0 | 1 | 1 | 0 |
| SS | 2.6 | 60.9 | 174.7 | 0 | 0 | 0 |
- monitor RPC error CWS1: TimeoutError('timed out') (x6)
- monitor RPC error CWS0: TimeoutError('timed out') (x5)

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 7.5 | 87 |
| ContestWebServer0 | 3 | 261.4 | 150 |
| ContestWebServer1 | 3 | 266.6 | 156 |
| EvaluationService0 | 5 | 157.2 | 96 |
| LogService0 | 0 | 51.8 | 84 |
| ProxyService0 | 4 | 16.7 | 93 |
| ScoringService0 | 1 | 35.2 | 95 |
| Worker0 | 1 | 97.8 | 91 |
| Worker1 | 1 | 96.1 | 88 |
| Worker2 | 1 | 101.4 | 87 |
| Worker3 | 1 | 96.3 | 95 |
| Worker4 | 1 | 104.2 | 90 |
| Worker5 | 1 | 101.3 | 86 |
| Worker6 | 2 | 99.0 | 94 |
| Worker7 | 1 | 100.7 | 87 |

PostgreSQL backends for cmsdb: max 27 (max_connections 100); max 'active' 4; max lock waits 1; longest idle-in-transaction 4.3 s.

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
- EvaluationService-0: missed operation(s) x25
- ProxyService-0: missed operation(s) x9
- ScoringService-0: missed operation(s) x9
- cms: missed operation(s) x43
- EvaluationService-0: sweeper found 12 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 12 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 1
- x1 db-N  | N-N-N N:N:N UTC [N] LOG:  duration: N ms  execute <unnamed>: SELECT pg_advisory_xact_lock(CAST($N AS integer), i
Errors/lock waits: 1
- x1 [N] LOG:  process N still waiting for ExclusiveLock on advisory lock [N,N,N,N] after N.N ms

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 101% max 314% (100% = 1 core); last mem 1.161GiB
- cmsload-fork-db-1: CPU mean 9% max 44% (100% = 1 core); last mem 108.1MiB
- cmsload-fork-driver-1: CPU mean 3% max 10% (100% = 1 core); last mem 1.383MiB
- cmsload-fork-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 33.53MiB
