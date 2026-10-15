# Run base-fork-2

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791340420.538416.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 255 | 0 | 0.010 | 0.014 | 0.018 | 0.030 |
| contest_page | steady | 113 | 0 | 0.011 | 0.016 | 0.019 | 0.019 |
| home_anon | login_burst | 250 | 0 | 0.006 | 0.007 | 0.039 | 0.201 |
| home_logged | login_burst | 250 | 0 | 0.005 | 0.006 | 0.008 | 0.010 |
| login | login_burst | 250 | 0 | 0.214 | 0.218 | 0.224 | 0.258 |
| notifications | login_burst | 509 | 0 | 0.009 | 0.011 | 0.012 | 0.053 |
| notifications | start_burst | 500 | 0 | 0.009 | 0.013 | 0.025 | 0.063 |
| notifications | steady | 9495 | 0 | 0.009 | 0.014 | 0.022 | 0.132 |
| notifications | end_burst | 2499 | 0 | 0.010 | 0.018 | 0.043 | 0.252 |
| notifications | drain | 570 | 0 | 0.011 | 0.016 | 0.028 | 0.108 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.006 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.002 | 0.002 | 0.002 |
| rws_scores | steady | 569 | 0 | 0.002 | 0.003 | 0.004 | 0.020 |
| rws_scores | end_burst | 150 | 0 | 0.002 | 0.003 | 0.004 | 0.005 |
| rws_scores | drain | 45 | 0 | 0.002 | 0.004 | 0.004 | 0.005 |
| statement | start_burst | 439 | 0 | 0.007 | 0.016 | 0.025 | 0.113 |
| status_poll | start_burst | 124 | 0 | 0.010 | 0.028 | 0.034 | 0.037 |
| status_poll | steady | 3854 | 0 | 0.010 | 0.032 | 0.041 | 0.147 |
| status_poll | end_burst | 2120 | 0 | 0.012 | 0.047 | 0.145 | 0.315 |
| status_poll | drain | 450 | 0 | 0.013 | 0.042 | 0.085 | 0.147 |
| submission_details | start_burst | 7 | 0 | 0.025 | 0.040 | 0.042 | 0.042 |
| submission_details | steady | 417 | 0 | 0.021 | 0.037 | 0.056 | 0.089 |
| submission_details | end_burst | 178 | 0 | 0.031 | 0.054 | 0.089 | 0.262 |
| submission_details | drain | 52 | 0 | 0.034 | 0.076 | 0.120 | 0.135 |
| submissions_list | start_burst | 6 | 0 | 0.024 | 0.034 | 0.034 | 0.034 |
| submissions_list | steady | 125 | 0 | 0.025 | 0.038 | 0.046 | 0.066 |
| submissions_page | start_burst | 28 | 0 | 0.025 | 0.073 | 0.137 | 0.154 |
| submissions_page | steady | 806 | 0 | 0.025 | 0.042 | 0.053 | 0.152 |
| submissions_page | end_burst | 494 | 0 | 0.037 | 0.058 | 0.172 | 0.254 |
| submit | start_burst | 28 | 0 | 0.022 | 0.043 | 0.053 | 0.054 |
| submit | steady | 806 | 0 | 0.020 | 0.030 | 0.033 | 0.063 |
| submit | end_burst | 494 | 0 | 0.023 | 0.050 | 0.121 | 0.291 |
| task_description | start_burst | 638 | 0 | 0.009 | 0.013 | 0.030 | 0.121 |
| task_description | steady | 250 | 0 | 0.010 | 0.018 | 0.030 | 0.070 |
| task_description | end_burst | 151 | 0 | 0.013 | 0.032 | 0.078 | 0.158 |

All phases: login p50 0.214 s, p95 0.218 s; submit p50 0.021 s, p95 0.035 s; submit in end_burst p95 0.050 s.

Peak CWS request rate: 93 req/s (1 s buckets); mean 15.2 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1328; rejected by CWS: 0; DB rows: 1328.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 28 | 11.3 | 19.3 | 19.8 | 13.3 | 25.9 | 26.9 |
| steady | 806 | 9.2 | 20.9 | 33.9 | 12.0 | 26.3 | 44.4 |
| end_burst | 494 | 10.2 | 39.7 | 55.4 | 13.5 | 50.0 | 71.6 |

All: server submit->scored p50 9.6 s, p95 29.8 s, max 55.4 s.
Last submission scored 52 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'wa_small': 139, 'wa_overflow': 184, 'ac': 401, 'ac_py': 128, 'tle': 175, 'ce': 92, 'wa_zero': 59, 're': 100, 'ac_java': 50}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1328}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=589 p50 1.0 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 1767 operations (max 74 distinct entries); workers busy mean 2.7, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:45/1.7 +1:61/2.9 +2:101/2.5 +3:24/2.5 +4:171/2.7 +5:36/1.2 +6:136/3.0 +7:130/2.5 +8:49/2.6 +9:47/2.7 +10:13/2.2 +11:191/4.2 +12:70/3.1 +13:121/2.1 +14:43/2.7 +15:65/2.2 +16:52/2.7 +17:63/3.5 +18:56/2.3 +19:35/2.4 +20:82/2.3 +21:74/2.7 +22:235/5.8 +23:185/6.1 +24:428/6.6 +25:1767/5.8 +26:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 1.7 | 3.5 | 0 | 0 | 0 |
| CWS0 | 0.9 | 37.0 | 110.2 | 0 | 0 | 0 |
| CWS1 | 0.9 | 38.0 | 137.4 | 0 | 0 | 0 |
| ES | 1.2 | 38.9 | 116.4 | 0 | 0 | 0 |
| PS | 0.9 | 1.8 | 5.0 | 0 | 0 | 0 |
| SS | 1.0 | 3.5 | 17.4 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.8 | 87 |
| ContestWebServer0 | 3 | 153.2 | 136 |
| ContestWebServer1 | 3 | 155.8 | 136 |
| EvaluationService0 | 6 | 165.2 | 93 |
| LogService0 | 0 | 54.9 | 84 |
| ProxyService0 | 1 | 11.2 | 91 |
| ScoringService0 | 1 | 29.6 | 89 |
| Worker0 | 1 | 125.6 | 90 |
| Worker1 | 1 | 116.8 | 96 |
| Worker2 | 1 | 115.3 | 87 |
| Worker3 | 2 | 115.8 | 88 |
| Worker4 | 1 | 120.0 | 86 |
| Worker5 | 1 | 111.4 | 90 |
| Worker6 | 1 | 125.5 | 89 |
| Worker7 | 1 | 122.9 | 88 |

PostgreSQL backends for cmsdb: max 24 (max_connections 100); max 'active' 2; max lock waits 0; longest idle-in-transaction 0.3 s.

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
- EvaluationService-0: missed operation(s) x16
- ProxyService-0: missed operation(s) x6
- ScoringService-0: missed operation(s) x6
- cms: missed operation(s) x28
- EvaluationService-0: sweeper found 0 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 0 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 1
- x1 [N] FATAL:  database "cmsdb" does not exist

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 165% max 806% (100% = 1 core); last mem 1.082GiB
- cmsload-fork-db-1: CPU mean 9% max 51% (100% = 1 core); last mem 105.9MiB
- cmsload-fork-driver-1: CPU mean 2% max 9% (100% = 1 core); last mem 1.238MiB
- cmsload-fork-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 28.79MiB
