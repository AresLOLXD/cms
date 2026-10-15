# Run base-fork-3

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791344193.236341.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 255 | 0 | 0.010 | 0.016 | 0.029 | 0.058 |
| contest_page | steady | 124 | 0 | 0.011 | 0.019 | 0.024 | 0.036 |
| home_anon | login_burst | 250 | 0 | 0.006 | 0.007 | 0.022 | 0.177 |
| home_logged | login_burst | 250 | 0 | 0.005 | 0.007 | 0.007 | 0.052 |
| login | login_burst | 250 | 0 | 0.215 | 0.218 | 0.226 | 0.262 |
| notifications | login_burst | 509 | 0 | 0.009 | 0.011 | 0.012 | 0.026 |
| notifications | start_burst | 500 | 0 | 0.008 | 0.013 | 0.019 | 0.064 |
| notifications | steady | 9496 | 0 | 0.009 | 0.014 | 0.022 | 0.122 |
| notifications | end_burst | 2498 | 0 | 0.010 | 0.017 | 0.041 | 0.147 |
| notifications | drain | 568 | 0 | 0.011 | 0.015 | 0.027 | 0.105 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.006 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.001 | 0.001 | 0.001 |
| rws_scores | steady | 570 | 0 | 0.002 | 0.003 | 0.004 | 0.019 |
| rws_scores | end_burst | 149 | 0 | 0.002 | 0.004 | 0.005 | 0.005 |
| rws_scores | drain | 45 | 0 | 0.002 | 0.003 | 0.004 | 0.004 |
| statement | start_burst | 429 | 0 | 0.007 | 0.017 | 0.054 | 0.116 |
| status_poll | start_burst | 130 | 0 | 0.009 | 0.025 | 0.044 | 0.070 |
| status_poll | steady | 4079 | 0 | 0.010 | 0.034 | 0.043 | 0.149 |
| status_poll | end_burst | 2560 | 0 | 0.012 | 0.043 | 0.073 | 0.217 |
| status_poll | drain | 432 | 0 | 0.013 | 0.045 | 0.051 | 0.056 |
| submission_details | start_burst | 7 | 0 | 0.025 | 0.041 | 0.045 | 0.046 |
| submission_details | steady | 408 | 0 | 0.022 | 0.035 | 0.043 | 0.132 |
| submission_details | end_burst | 183 | 0 | 0.032 | 0.047 | 0.077 | 0.091 |
| submission_details | drain | 53 | 0 | 0.032 | 0.041 | 0.042 | 0.042 |
| submissions_list | start_burst | 7 | 0 | 0.026 | 0.030 | 0.031 | 0.031 |
| submissions_list | steady | 114 | 0 | 0.026 | 0.040 | 0.048 | 0.068 |
| submissions_page | start_burst | 31 | 0 | 0.024 | 0.067 | 0.096 | 0.097 |
| submissions_page | steady | 839 | 0 | 0.025 | 0.042 | 0.049 | 0.166 |
| submissions_page | end_burst | 491 | 0 | 0.038 | 0.060 | 0.135 | 0.169 |
| submit | start_burst | 31 | 0 | 0.021 | 0.042 | 0.053 | 0.055 |
| submit | steady | 839 | 0 | 0.020 | 0.032 | 0.047 | 0.143 |
| submit | end_burst | 491 | 0 | 0.023 | 0.044 | 0.107 | 0.216 |
| task_description | start_burst | 627 | 0 | 0.009 | 0.015 | 0.046 | 0.078 |
| task_description | steady | 256 | 0 | 0.011 | 0.018 | 0.023 | 0.036 |
| task_description | end_burst | 149 | 0 | 0.013 | 0.024 | 0.058 | 0.071 |

All phases: login p50 0.215 s, p95 0.218 s; submit p50 0.021 s, p95 0.034 s; submit in end_burst p95 0.044 s.

Peak CWS request rate: 98 req/s (1 s buckets); mean 15.6 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1361; rejected by CWS: 0; DB rows: 1361.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 31 | 11.9 | 21.7 | 23.0 | 17.2 | 27.6 | 29.6 |
| steady | 839 | 9.6 | 21.8 | 29.5 | 12.5 | 25.9 | 42.7 |
| end_burst | 491 | 20.0 | 42.3 | 54.0 | 23.6 | 57.6 | 70.3 |

All: server submit->scored p50 11.0 s, p95 37.0 s, max 54.0 s.
Last submission scored 51 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 85, 'ac_java': 52, 'tle': 184, 'wa_small': 128, 're': 112, 'wa_zero': 74, 'ac': 393, 'wa_overflow': 193, 'ac_py': 140}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1361}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=575 p50 1.0 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 1932 operations (max 83 distinct entries); workers busy mean 2.7, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:38/1.7 +1:91/3.0 +2:80/2.3 +3:91/3.2 +4:90/2.6 +5:40/2.4 +6:19/1.2 +7:134/3.8 +8:80/2.2 +9:251/5.1 +10:89/2.4 +11:51/3.1 +12:108/2.5 +13:97/3.6 +14:35/1.9 +15:46/1.2 +16:80/2.6 +17:52/2.2 +18:54/3.0 +19:58/2.3 +20:159/3.3 +21:137/4.3 +22:196/5.1 +23:161/3.3 +24:1239/7.5 +25:1932/5.8 +26:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 1.9 | 6.5 | 0 | 0 | 0 |
| CWS0 | 0.9 | 27.2 | 81.8 | 0 | 0 | 0 |
| CWS1 | 0.9 | 21.6 | 51.2 | 0 | 0 | 0 |
| ES | 1.2 | 18.2 | 98.1 | 0 | 0 | 0 |
| PS | 0.9 | 2.2 | 4.1 | 0 | 0 | 0 |
| SS | 1.0 | 8.9 | 19.2 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.7 | 86 |
| ContestWebServer0 | 3 | 157.5 | 137 |
| ContestWebServer1 | 3 | 159.8 | 138 |
| EvaluationService0 | 8 | 166.2 | 93 |
| LogService0 | 0 | 55.4 | 84 |
| ProxyService0 | 2 | 11.8 | 91 |
| ScoringService0 | 2 | 31.2 | 89 |
| Worker0 | 1 | 132.6 | 94 |
| Worker1 | 1 | 119.0 | 90 |
| Worker2 | 2 | 119.1 | 90 |
| Worker3 | 1 | 120.1 | 94 |
| Worker4 | 2 | 114.9 | 92 |
| Worker5 | 1 | 122.8 | 87 |
| Worker6 | 1 | 124.7 | 89 |
| Worker7 | 1 | 124.5 | 89 |

PostgreSQL backends for cmsdb: max 27 (max_connections 100); max 'active' 3; max lock waits 0; longest idle-in-transaction 0.4 s.

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
Errors/lock waits: 4
- x3 [N] FATAL:  the database system is shutting down
- x1 [N] FATAL:  database "cmsdb" does not exist

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 156% max 753% (100% = 1 core); last mem 1.124GiB
- cmsload-fork-db-1: CPU mean 9% max 38% (100% = 1 core); last mem 108.1MiB
- cmsload-fork-driver-1: CPU mean 2% max 7% (100% = 1 core); last mem 1.129MiB
- cmsload-fork-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 28.56MiB
