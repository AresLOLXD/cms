# Run ci-3-fork

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791394747.241744.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 253 | 0 | 0.011 | 0.116 | 0.230 | 0.419 |
| contest_page | steady | 85 | 0 | 0.093 | 0.735 | 1.644 | 3.696 |
| home_anon | login_burst | 250 | 0 | 0.006 | 0.008 | 0.043 | 0.163 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.009 | 0.012 | 0.022 |
| login | login_burst | 250 | 0 | 0.278 | 0.292 | 0.304 | 0.324 |
| notifications | login_burst | 508 | 0 | 0.009 | 0.012 | 0.017 | 0.027 |
| notifications | start_burst | 500 | 0 | 0.010 | 0.070 | 0.198 | 0.340 |
| notifications | steady | 9405 | 0 | 0.089 | 0.959 | 2.259 | 4.065 |
| notifications | end_burst | 2241 | 0 | 3.392 | 13.165 | 16.884 | 22.939 |
| notifications | drain | 8034 | 0 | 0.041 | 1.752 | 11.508 | 20.321 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.007 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.003 | 0.003 | 0.004 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.012 | 0.029 | 0.066 |
| rws_scores | end_burst | 149 | 0 | 0.003 | 0.014 | 0.023 | 0.080 |
| rws_scores | drain | 502 | 0 | 0.003 | 0.013 | 0.024 | 0.043 |
| statement | start_burst | 433 | 0 | 0.009 | 0.163 | 0.379 | 0.557 |
| status_poll | start_burst | 102 | 0 | 0.012 | 0.128 | 0.251 | 0.331 |
| status_poll | steady | 5918 | 0 | 0.074 | 0.756 | 1.986 | 4.591 |
| status_poll | end_burst | 3050 | 0 | 3.979 | 14.548 | 19.494 | 27.272 |
| status_poll | drain | 2846 | 0 | 0.194 | 12.414 | 15.759 | 22.464 |
| submission_details | start_burst | 8 | 0 | 0.028 | 0.155 | 0.173 | 0.178 |
| submission_details | steady | 274 | 0 | 0.148 | 0.735 | 1.346 | 2.207 |
| submission_details | end_burst | 99 | 0 | 4.060 | 16.507 | 19.657 | 19.783 |
| submission_details | drain | 192 | 0 | 0.125 | 9.975 | 13.897 | 17.368 |
| submissions_list | start_burst | 7 | 0 | 0.028 | 0.117 | 0.145 | 0.152 |
| submissions_list | steady | 94 | 0 | 0.208 | 0.901 | 1.428 | 1.844 |
| submissions_page | start_burst | 25 | 0 | 0.034 | 0.183 | 0.219 | 0.229 |
| submissions_page | steady | 588 | 0 | 0.155 | 0.760 | 1.193 | 2.372 |
| submissions_page | end_burst | 419 | 0 | 4.617 | 16.639 | 19.868 | 23.158 |
| submissions_page | drain | 45 | 0 | 13.101 | 18.225 | 21.091 | 23.020 |
| submit | start_burst | 25 | 0 | 0.019 | 0.090 | 0.125 | 0.136 |
| submit | steady | 588 | 0 | 0.116 | 0.607 | 1.697 | 2.632 |
| submit | end_burst | 464 | 0 | 5.160 | 16.614 | 21.168 | 26.950 |
| submit | drain | 17 | 0 | 13.415 | 17.492 | 18.510 | 18.764 |
| task_description | start_burst | 631 | 0 | 0.010 | 0.116 | 0.185 | 0.307 |
| task_description | steady | 179 | 0 | 0.068 | 0.728 | 1.137 | 4.316 |
| task_description | end_burst | 135 | 0 | 5.148 | 16.544 | 18.707 | 19.790 |

All phases: login p50 0.278 s, p95 0.292 s; submit p50 0.282 s, p95 13.969 s; submit in end_burst p95 16.614 s.

Peak CWS request rate: 105 req/s (1 s buckets); mean 14.5 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1094; rejected by CWS: 17; DB rows: 1077.
  - rejected a012 cadena status=302 location=../../../loada
  - rejected a123 cadena status=302 location=../../../loada
  - rejected a023 libre status=302 location=../../../loada
  - rejected a121 libre status=302 location=../../../loada
  - rejected a118 suma status=302 location=../../../loada
  - rejected a007 suma status=302 location=../../../loada
  - rejected a147 suma status=302 location=../../../loada
  - rejected a081 umbral status=302 location=../../../loada
  - rejected b072 sumab status=302 location=../../../loadb
  - rejected b036 umbralb status=302 location=../../../loadb

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 25 | 12.7 | 22.8 | 23.8 | 16.5 | 27.6 | 30.9 |
| steady | 588 | 127.4 | 167.1 | 176.6 | 158.3 | 222.2 | 265.2 |
| end_burst | 464 | 371.9 | 682.0 | 700.2 | 445.0 | 825.7 | 989.9 |

All: server submit->scored p50 150.5 s, p95 628.4 s, max 700.2 s.
Last submission scored 697 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 2
  - a108 libre wa_overflow expected 50.0 got 25.0 (id 349, evaluations 24)
  - a019 cadena ac_py expected 100.0 got 80.0 (id 841, evaluations 34)
Mix: {'wa_overflow': 148, 'wa_small': 126, 'ac': 306, 'tle': 149, 'ac_py': 117, 're': 88, 'ac_java': 28, 'ce': 70, 'wa_zero': 62}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1077}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=520 p50 1.0 p95 2.0 max 2.2 s

## Internals (monitor, 2 s samples)

ES queue: max 7706 operations (max 318 distinct entries); workers busy mean 6.1, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:31/2.0 +1:275/6.0 +2:269/8.0 +3:243/8.0 +4:472/8.0 +5:778/8.0 +6:977/8.0 +7:1357/8.0 +8:1004/8.0 +9:1039/8.0 +10:926/8.0 +11:939/8.0 +12:1145/8.0 +13:1363/8.0 +14:1480/8.0 +15:1318/8.0 +16:971/8.0 +17:966/8.0 +18:958/8.0 +19:926/8.0 +20:884/8.0 +21:1595/8.0 +22:2983/8.0 +23:4868/8.0 +24:5418/8.0 +25:7706/8.0 +26:7381/8.0 +27:6931/8.0 +28:6306/8.0 +29:5456/8.0 +30:4906/8.0 +31:4356/8.0 +32:3756/8.0 +33:3031/8.0 +34:2056/8.0 +35:1206/8.0 +36:406/4.6 +37:0/0.0 +38:0/0.0 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 2.4 | 55.7 | 195.3 | 0 | 0 | 0 |
| CWS0 | 4.5 | 2487.9 | 9396.6 | 166 | 48 | 7 |
| CWS1 | 3.5 | 2024.4 | 7773.9 | 116 | 33 | 14 |
| ES | 2.9 | 869.0 | 5146.3 | 20 | 10 | 0 |
| PS | 2.3 | 45.0 | 437.4 | 1 | 0 | 0 |
| SS | 2.5 | 56.7 | 3487.3 | 1 | 1 | 0 |
- monitor RPC error CWS1: TimeoutError('timed out') (x14)
- monitor RPC error CWS0: TimeoutError('timed out') (x7)

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 7.5 | 88 |
| ContestWebServer0 | 3 | 258.0 | 154 |
| ContestWebServer1 | 3 | 265.9 | 154 |
| EvaluationService0 | 8 | 156.3 | 96 |
| LogService0 | 0 | 52.1 | 84 |
| ProxyService0 | 4 | 15.4 | 92 |
| ScoringService0 | 2 | 37.3 | 95 |
| Worker0 | 2 | 97.8 | 87 |
| Worker1 | 1 | 96.0 | 90 |
| Worker2 | 1 | 102.5 | 87 |
| Worker3 | 1 | 98.7 | 93 |
| Worker4 | 1 | 99.7 | 94 |
| Worker5 | 1 | 98.0 | 89 |
| Worker6 | 2 | 99.7 | 87 |
| Worker7 | 1 | 99.9 | 92 |

PostgreSQL backends for cmsdb: max 27 (max_connections 100); max 'active' 3; max lock waits 1; longest idle-in-transaction 95.2 s.

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
- EvaluationService-0: missed operation(s) x24
- ProxyService-0: missed operation(s) x8
- ScoringService-0: missed operation(s) x9
- cms: missed operation(s) x41
- EvaluationService-0: sweeper found 20 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 20 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 5
- x5 db-N  | N-N-N N:N:N UTC [N] LOG:  duration: N ms  execute <unnamed>: SELECT pg_advisory_xact_lock(CAST($N AS integer), i
Errors/lock waits: 5
- x5 [N] LOG:  process N still waiting for ExclusiveLock on advisory lock [N,N,N,N] after N.N ms

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 96% max 334% (100% = 1 core); last mem 1.183GiB
- cmsload-fork-db-1: CPU mean 9% max 31% (100% = 1 core); last mem 115.4MiB
- cmsload-fork-driver-1: CPU mean 3% max 8% (100% = 1 core); last mem 1.309MiB
- cmsload-fork-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 28.44MiB
