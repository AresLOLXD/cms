# Run ramp-fork-1000

Target fork, profile portable.
Users: 1000 (loada 700, loadb 300). Contest 1500 s; start 1791352439.158899.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 1016 | 0 | 0.017 | 0.464 | 0.793 | 1.089 |
| contest_page | steady | 331 | 0 | 0.021 | 0.040 | 0.082 | 0.123 |
| home_anon | login_burst | 1000 | 0 | 0.006 | 0.009 | 0.017 | 0.250 |
| home_logged | login_burst | 1000 | 0 | 0.008 | 0.011 | 0.014 | 0.066 |
| login | login_burst | 1000 | 0 | 0.269 | 0.293 | 0.332 | 0.390 |
| notifications | login_burst | 1987 | 0 | 0.009 | 0.012 | 0.018 | 0.113 |
| notifications | start_burst | 2000 | 0 | 0.017 | 0.325 | 0.653 | 1.151 |
| notifications | steady | 37976 | 0 | 0.017 | 0.038 | 0.074 | 0.518 |
| notifications | end_burst | 9783 | 0 | 0.028 | 2.070 | 5.997 | 10.447 |
| notifications | drain | 39808 | 0 | 0.012 | 0.025 | 1.691 | 4.938 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.003 | 0.011 |
| rws_scores | start_burst | 30 | 0 | 0.002 | 0.003 | 0.004 | 0.004 |
| rws_scores | steady | 567 | 0 | 0.005 | 0.014 | 0.026 | 0.223 |
| rws_scores | end_burst | 148 | 0 | 0.010 | 0.029 | 0.042 | 0.090 |
| rws_scores | drain | 602 | 0 | 0.007 | 0.013 | 0.061 | 0.155 |
| statement | start_burst | 1756 | 0 | 0.022 | 0.922 | 1.165 | 1.662 |
| status_poll | start_burst | 469 | 0 | 0.018 | 0.053 | 0.117 | 0.398 |
| status_poll | steady | 23748 | 0 | 0.019 | 0.054 | 0.092 | 0.562 |
| status_poll | end_burst | 14730 | 0 | 0.030 | 2.371 | 5.304 | 12.173 |
| status_poll | drain | 10954 | 0 | 0.018 | 2.304 | 3.679 | 4.735 |
| submission_details | start_burst | 28 | 0 | 0.049 | 0.069 | 0.100 | 0.111 |
| submission_details | steady | 928 | 0 | 0.048 | 0.088 | 0.178 | 0.210 |
| submission_details | end_burst | 228 | 0 | 0.063 | 1.256 | 2.006 | 2.640 |
| submission_details | drain | 919 | 0 | 0.038 | 0.056 | 0.341 | 4.295 |
| submissions_list | start_burst | 11 | 0 | 0.048 | 0.261 | 0.425 | 0.466 |
| submissions_list | steady | 294 | 0 | 0.049 | 0.083 | 0.185 | 0.208 |
| submissions_page | start_burst | 107 | 0 | 0.056 | 0.266 | 0.477 | 0.581 |
| submissions_page | steady | 2277 | 0 | 0.055 | 0.087 | 0.151 | 0.299 |
| submissions_page | end_burst | 1789 | 0 | 0.081 | 2.520 | 3.990 | 5.479 |
| submissions_page | drain | 16 | 0 | 2.863 | 3.694 | 3.719 | 3.726 |
| submit | start_burst | 107 | 0 | 0.036 | 0.377 | 0.636 | 0.667 |
| submit | steady | 2277 | 0 | 0.035 | 0.064 | 0.126 | 0.587 |
| submit | end_burst | 1829 | 0 | 0.069 | 2.776 | 5.813 | 10.514 |
| submit | drain | 6 | 0 | 3.032 | 3.594 | 3.605 | 3.608 |
| task_description | start_burst | 2539 | 0 | 0.017 | 0.475 | 0.773 | 1.100 |
| task_description | steady | 684 | 0 | 0.021 | 0.043 | 0.112 | 0.232 |
| task_description | end_burst | 535 | 0 | 0.040 | 2.466 | 4.610 | 8.027 |

All phases: login p50 0.269 s, p95 0.293 s; submit p50 0.041 s, p95 1.612 s; submit in end_burst p95 2.776 s.

Peak CWS request rate: 336 req/s (1 s buckets); mean 57.3 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 4219; rejected by CWS: 30; DB rows: 4189.
  - rejected a648 libre status=302 location=../../../loada
  - rejected a153 umbral status=302 location=../../../loada
  - rejected a600 libre status=302 location=../../../loada
  - rejected b105 mulb status=302 location=../../../loadb
  - rejected a329 umbral status=302 location=../../../loada
  - rejected a174 umbral status=302 location=../../../loada
  - rejected a636 umbral status=302 location=../../../loada
  - rejected b104 mulb status=302 location=../../../loadb
  - rejected a593 cadena status=302 location=../../../loada
  - rejected a622 suma status=302 location=../../../loada

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 107 | 14.2 | 23.5 | 27.3 | 18.6 | 33.4 | 37.3 |
| steady | 2277 | 191.4 | 268.8 | 327.7 | 220.3 | 336.1 | 469.5 |
| end_burst | 1805 | 482.2 | 786.4 | 838.5 | 591.4 | 1022.6 | 1194.5 |

All: server submit->scored p50 209.5 s, p95 741.7 s, max 838.5 s.
Last submission scored 838 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'tle': 603, 'ce': 306, 'wa_overflow': 589, 'ac': 1194, 'ac_py': 435, 're': 354, 'wa_small': 386, 'ac_java': 163, 'wa_zero': 189}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 4189}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 2800 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=2004 p50 1.1 p95 2.0 max 2.2 s

## Internals (monitor, 2 s samples)

ES queue: max 39550 operations (max 1653 distinct entries); workers busy mean 6.3, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:482/4.7 +1:1857/8.0 +2:3215/8.0 +3:4040/8.0 +4:4525/8.0 +5:5625/8.0 +6:6008/8.0 +7:6986/8.0 +8:7112/8.0 +9:7248/8.0 +10:7318/8.0 +11:7180/8.0 +12:7443/8.0 +13:7385/8.0 +14:7455/8.0 +15:7253/8.0 +16:7270/8.0 +17:7562/8.0 +18:7774/8.0 +19:7951/8.0 +20:9410/8.0 +21:11829/8.0 +22:15821/8.0 +23:22408/8.0 +24:36193/8.0 +25:39550/8.0 +26:37300/8.0 +27:34550/8.0 +28:32075/8.0 +29:29075/8.0 +30:25825/8.0 +31:22475/8.0 +32:19450/8.0 +33:16525/8.0 +34:13650/8.0 +35:10800/8.0 +36:8125/8.0 +37:5300/8.0 +38:1900/6.9 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0 +43:0/0.0 +44:0/0.0 +45:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 1.2 | 6.0 | 9.9 | 0 | 0 | 0 |
| CWS0 | 1.3 | 162.3 | 2241.2 | 10 | 6 | 0 |
| CWS1 | 1.3 | 166.6 | 3482.1 | 11 | 5 | 1 |
| CWS2 | 1.3 | 202.4 | 9518.4 | 10 | 6 | 0 |
| CWS3 | 1.3 | 161.1 | 4589.7 | 13 | 5 | 0 |
| ES | 1.6 | 46.5 | 331.2 | 2 | 0 | 0 |
| PS | 1.2 | 6.9 | 13.9 | 0 | 0 | 0 |
| SS | 1.3 | 16.8 | 25.5 | 0 | 0 | 0 |
- monitor RPC error CWS1: TimeoutError('timed out') (x1)

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 9.3 | 86 |
| ContestWebServer0 | 3 | 593.0 | 157 |
| ContestWebServer1 | 3 | 592.9 | 155 |
| ContestWebServer2 | 3 | 598.6 | 161 |
| ContestWebServer3 | 3 | 598.5 | 161 |
| EvaluationService0 | 5 | 685.3 | 128 |
| LogService0 | 0 | 233.2 | 84 |
| ProxyService0 | 2 | 64.9 | 100 |
| ScoringService0 | 2 | 146.8 | 107 |
| Worker0 | 1 | 487.1 | 89 |
| Worker1 | 1 | 480.0 | 94 |
| Worker2 | 2 | 454.5 | 87 |
| Worker3 | 1 | 497.0 | 89 |
| Worker4 | 1 | 480.3 | 89 |
| Worker5 | 1 | 466.2 | 92 |
| Worker6 | 2 | 483.9 | 88 |
| Worker7 | 1 | 477.8 | 87 |

PostgreSQL backends for cmsdb: max 32 (max_connections 100); max 'active' 5; max lock waits 3; longest idle-in-transaction 23.7 s.

## Service logs

Log files: 18

| service | WARNING | ERROR | CRITICAL |
|---|---|---|---|
| AdminWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-1 | 0 | 0 | 0 |
| ContestWebServer-2 | 0 | 0 | 0 |
| ContestWebServer-3 | 0 | 0 | 0 |
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
- EvaluationService-0: missed operation(s) x28
- ProxyService-0: missed operation(s) x9
- ScoringService-0: missed operation(s) x10
- Worker-2: IntegrityError x1
- cms: IntegrityError x1
- cms: missed operation(s) x47
- EvaluationService-0: sweeper found 10 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 1 missed operations in total
- cms: sweeper found 11 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 30
- x23 COMMIT
- x7 db-N  | N-N-N N:N:N UTC [N] LOG:  duration: N ms  execute <unnamed>: SELECT pg_advisory_xact_lock(CAST($N AS integer), i
Errors/lock waits: 6
- x4 [N] LOG:  process N still waiting for ExclusiveLock on advisory lock [N,N,N,N] after N.N ms
- x1 [N] FATAL:  database "cmsdb" does not exist
- x1 [N] ERROR:  duplicate key value violates unique constraint "fsobjects_pkey"

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 414% max 845% (100% = 1 core); last mem 1.484GiB
- cmsload-fork-db-1: CPU mean 27% max 93% (100% = 1 core); last mem 145.9MiB
- cmsload-fork-driver-1: CPU mean 12% max 66% (100% = 1 core); last mem 1.477MiB
- cmsload-fork-ranking-1: CPU mean 0% max 3% (100% = 1 core); last mem 41.26MiB
