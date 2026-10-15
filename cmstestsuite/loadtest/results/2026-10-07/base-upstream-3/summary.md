# Run base-upstream-3

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791346067.98534.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 255 | 0 | 0.011 | 0.017 | 0.019 | 0.030 |
| contest_page | steady | 117 | 0 | 0.012 | 0.024 | 0.037 | 0.061 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.200 | 0.395 | 0.677 |
| home_logged | login_burst | 250 | 0 | 0.006 | 0.217 | 0.430 | 0.663 |
| login | login_burst | 250 | 0 | 0.216 | 0.547 | 0.675 | 0.884 |
| notifications | login_burst | 506 | 0 | 0.011 | 0.258 | 0.525 | 0.807 |
| notifications | start_burst | 500 | 0 | 0.011 | 0.048 | 0.052 | 0.073 |
| notifications | steady | 9490 | 0 | 0.011 | 0.023 | 0.042 | 0.147 |
| notifications | end_burst | 2497 | 0 | 0.015 | 0.037 | 0.064 | 0.547 |
| notifications | drain | 870 | 0 | 0.015 | 0.027 | 0.050 | 0.110 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.006 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.001 | 0.002 | 0.002 |
| rws_scores | steady | 569 | 0 | 0.002 | 0.003 | 0.004 | 0.010 |
| rws_scores | end_burst | 150 | 0 | 0.003 | 0.004 | 0.007 | 0.013 |
| rws_scores | drain | 63 | 0 | 0.003 | 0.004 | 0.006 | 0.007 |
| statement | start_burst | 426 | 0 | 0.017 | 0.030 | 0.060 | 0.114 |
| status_poll | start_burst | 119 | 0 | 0.012 | 0.053 | 0.054 | 0.056 |
| status_poll | steady | 3975 | 0 | 0.014 | 0.054 | 0.069 | 0.162 |
| status_poll | end_burst | 2631 | 0 | 0.018 | 0.072 | 0.289 | 0.595 |
| status_poll | drain | 666 | 0 | 0.019 | 0.059 | 0.077 | 0.113 |
| submission_details | start_burst | 11 | 0 | 0.027 | 0.037 | 0.039 | 0.039 |
| submission_details | steady | 412 | 0 | 0.027 | 0.055 | 0.075 | 0.096 |
| submission_details | end_burst | 201 | 0 | 0.041 | 0.073 | 0.112 | 0.463 |
| submission_details | drain | 67 | 0 | 0.043 | 0.060 | 0.090 | 0.122 |
| submissions_list | start_burst | 3 | 0 | 0.029 | 0.039 | 0.040 | 0.040 |
| submissions_list | steady | 135 | 0 | 0.030 | 0.050 | 0.056 | 0.064 |
| submissions_page | start_burst | 27 | 0 | 0.029 | 0.084 | 0.097 | 0.100 |
| submissions_page | steady | 817 | 0 | 0.034 | 0.060 | 0.077 | 0.148 |
| submissions_page | end_burst | 518 | 0 | 0.053 | 0.152 | 0.525 | 0.760 |
| submit | start_burst | 27 | 0 | 0.025 | 0.040 | 0.045 | 0.046 |
| submit | steady | 817 | 0 | 0.025 | 0.042 | 0.053 | 0.150 |
| submit | end_burst | 518 | 0 | 0.034 | 0.147 | 0.637 | 0.840 |
| task_description | start_burst | 623 | 0 | 0.010 | 0.015 | 0.033 | 0.059 |
| task_description | steady | 234 | 0 | 0.013 | 0.025 | 0.030 | 0.047 |
| task_description | end_burst | 160 | 0 | 0.018 | 0.137 | 0.390 | 0.501 |

All phases: login p50 0.216 s, p95 0.547 s; submit p50 0.027 s, p95 0.059 s; submit in end_burst p95 0.147 s.

Peak CWS request rate: 87 req/s (1 s buckets); mean 15.6 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1362; rejected by CWS: 0; DB rows: 1362.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 27 | 14.5 | 21.6 | 22.4 | 18.0 | 29.8 | 33.3 |
| steady | 817 | 9.6 | 21.9 | 32.5 | 12.7 | 26.5 | 45.5 |
| end_burst | 518 | 20.2 | 62.7 | 77.8 | 24.3 | 77.0 | 106.7 |

All: server submit->scored p50 11.4 s, p95 49.7 s, max 77.8 s.
Last submission scored 75 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ac_py': 138, 'wa_zero': 65, 'tle': 199, 'ac': 375, 'wa_overflow': 189, 're': 113, 'wa_small': 136, 'ac_java': 56, 'ce': 91}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1362}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=577 p50 1.1 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 2503 operations (max 105 distinct entries); workers busy mean 2.8, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:117/1.7 +1:32/3.1 +2:39/1.7 +3:96/2.7 +4:39/2.4 +5:21/1.5 +6:149/1.8 +7:84/2.9 +8:60/2.2 +9:87/2.8 +10:75/3.1 +11:94/3.6 +12:91/1.5 +13:104/3.0 +14:186/3.4 +15:81/3.4 +16:106/3.2 +17:91/3.6 +18:126/3.4 +19:86/2.4 +20:202/3.5 +21:125/4.1 +22:503/5.4 +23:307/5.6 +24:1501/5.7 +25:2503/8.0 +26:193/1.1 +27:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 3.1 | 12.7 | 0 | 0 | 0 |
| CWS0 | 0.9 | 37.0 | 200.1 | 0 | 0 | 0 |
| CWS1 | 0.9 | 41.7 | 205.5 | 0 | 0 | 0 |
| ES | 1.2 | 3.6 | 5.6 | 0 | 0 | 0 |
| PS | 0.9 | 3.2 | 6.2 | 0 | 0 | 0 |
| SS | 0.9 | 4.7 | 23.1 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.1 | 73 |
| ContestWebServer0 | 11 | 194.7 | 120 |
| ContestWebServer1 | 5 | 202.2 | 120 |
| EvaluationService0 | 5 | 181.6 | 72 |
| LogService0 | 0 | 79.5 | 66 |
| ProxyService0 | 2 | 14.7 | 77 |
| ScoringService0 | 1 | 35.2 | 72 |
| Worker0 | 1 | 124.4 | 73 |
| Worker1 | 2 | 128.2 | 80 |
| Worker2 | 1 | 133.2 | 73 |
| Worker3 | 1 | 123.5 | 72 |
| Worker4 | 1 | 120.3 | 77 |
| Worker5 | 2 | 124.3 | 72 |
| Worker6 | 1 | 136.9 | 81 |
| Worker7 | 1 | 138.0 | 73 |

PostgreSQL backends for cmsdb: max 33 (max_connections 100); max 'active' 4; max lock waits 0; longest idle-in-transaction 1.1 s.

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
- EvaluationService-0: missed operation(s) x17
- ProxyService-0: missed operation(s) x6
- ScoringService-0: missed operation(s) x6
- cms: missed operation(s) x29
- EvaluationService-0: sweeper found 2 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 2 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 1
- x1 COMMIT
Errors/lock waits: 1
- x1 [N] FATAL:  the database system is shutting down

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 169% max 741% (100% = 1 core); last mem 936.7MiB
- cmsload-upstream-db-1: CPU mean 11% max 50% (100% = 1 core); last mem 114.5MiB
- cmsload-upstream-driver-1: CPU mean 2% max 9% (100% = 1 core); last mem 1.133MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 27.45MiB
