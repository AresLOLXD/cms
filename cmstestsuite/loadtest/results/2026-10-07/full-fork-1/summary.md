# Run full-fork-1

Target fork, profile full.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791347992.26532.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 252 | 0 | 0.010 | 0.017 | 0.027 | 0.063 |
| contest_page | steady | 126 | 0 | 0.011 | 0.020 | 0.031 | 0.032 |
| home_anon | login_burst | 250 | 0 | 0.006 | 0.007 | 0.043 | 0.190 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.008 | 0.009 | 0.011 |
| login | login_burst | 250 | 0 | 0.257 | 0.265 | 0.269 | 0.285 |
| notifications | login_burst | 509 | 0 | 0.009 | 0.011 | 0.013 | 0.061 |
| notifications | start_burst | 499 | 0 | 0.008 | 0.015 | 0.023 | 0.076 |
| notifications | steady | 9495 | 0 | 0.009 | 0.018 | 0.028 | 0.181 |
| notifications | end_burst | 2498 | 0 | 0.011 | 0.028 | 0.075 | 0.358 |
| notifications | drain | 452 | 0 | 0.010 | 0.024 | 0.052 | 0.114 |
| rws_scores | login_burst | 188 | 0 | 0.024 | 0.042 | 0.042 | 0.042 |
| rws_scores | start_burst | 60 | 0 | 0.021 | 0.042 | 0.042 | 0.043 |
| rws_scores | steady | 1130 | 0 | 0.002 | 0.042 | 0.042 | 0.043 |
| rws_scores | end_burst | 300 | 0 | 0.002 | 0.005 | 0.008 | 0.016 |
| rws_scores | drain | 74 | 0 | 0.002 | 0.004 | 0.005 | 0.005 |
| statement | start_burst | 434 | 0 | 0.008 | 0.022 | 0.054 | 0.096 |
| status_poll | start_burst | 135 | 0 | 0.009 | 0.018 | 0.025 | 0.047 |
| status_poll | steady | 5043 | 0 | 0.009 | 0.032 | 0.054 | 0.202 |
| status_poll | end_burst | 2896 | 0 | 0.015 | 0.057 | 0.097 | 0.389 |
| status_poll | drain | 315 | 0 | 0.019 | 0.059 | 0.079 | 0.152 |
| submission_details | start_burst | 4 | 0 | 0.026 | 0.031 | 0.031 | 0.031 |
| submission_details | steady | 403 | 0 | 0.026 | 0.049 | 0.060 | 0.109 |
| submission_details | end_burst | 210 | 0 | 0.040 | 0.077 | 0.112 | 0.320 |
| submission_details | drain | 50 | 0 | 0.042 | 0.069 | 0.089 | 0.105 |
| submissions_list | start_burst | 7 | 0 | 0.023 | 0.081 | 0.093 | 0.096 |
| submissions_list | steady | 129 | 0 | 0.026 | 0.050 | 0.075 | 0.086 |
| submissions_page | start_burst | 31 | 0 | 0.027 | 0.046 | 0.093 | 0.112 |
| submissions_page | steady | 800 | 0 | 0.030 | 0.055 | 0.106 | 0.181 |
| submissions_page | end_burst | 507 | 0 | 0.044 | 0.079 | 0.141 | 0.223 |
| submit | start_burst | 31 | 0 | 0.022 | 0.047 | 0.063 | 0.066 |
| submit | steady | 800 | 0 | 0.021 | 0.039 | 0.047 | 0.093 |
| submit | end_burst | 507 | 0 | 0.030 | 0.075 | 0.175 | 0.419 |
| task_description | start_burst | 625 | 0 | 0.009 | 0.015 | 0.019 | 0.069 |
| task_description | steady | 216 | 0 | 0.011 | 0.023 | 0.028 | 0.033 |
| task_description | end_burst | 157 | 0 | 0.017 | 0.037 | 0.110 | 0.202 |

All phases: login p50 0.257 s, p95 0.265 s; submit p50 0.022 s, p95 0.048 s; submit in end_burst p95 0.075 s.

Peak CWS request rate: 89 req/s (1 s buckets); mean 16.3 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1338; rejected by CWS: 0; DB rows: 1338.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 31 | 20.8 | 36.7 | 48.5 | 26.1 | 46.1 | 55.3 |
| steady | 800 | 18.6 | 45.9 | 67.5 | 24.0 | 58.5 | 81.8 |
| end_burst | 507 | 18.3 | 44.1 | 74.0 | 23.0 | 54.8 | 96.9 |

All: server submit->scored p50 18.5 s, p95 44.9 s, max 74.0 s.
Last submission scored 45 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 77, 'ac_py': 131, 'wa_small': 147, 'wa_overflow': 200, 'tle': 196, 'wa_zero': 61, 'ac': 373, 're': 98, 'ac_java': 55}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1338}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada, loadb): 925 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=790 p50 0.9 p95 1.9 max 2.1 s

## Internals (monitor, 2 s samples)

ES queue: max 161 operations (max 29 distinct entries); workers busy mean 2.0, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:0/0.4 +1:24/2.3 +2:4/0.4 +3:21/1.8 +4:24/1.5 +5:37/2.4 +6:32/2.1 +7:35/0.9 +8:29/1.3 +9:15/0.7 +10:14/2.1 +11:23/2.3 +12:15/1.2 +13:23/1.1 +14:30/2.2 +15:36/1.6 +16:38/2.3 +17:11/1.8 +18:31/1.9 +19:31/1.8 +20:28/1.4 +21:57/4.6 +22:43/4.9 +23:79/4.6 +24:91/7.8 +25:161/3.3 +26:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.8 | 2.6 | 5.4 | 0 | 0 | 0 |
| CWS0 | 0.9 | 44.8 | 164.3 | 0 | 0 | 0 |
| CWS1 | 0.9 | 36.2 | 105.6 | 0 | 0 | 0 |
| ES | 1.1 | 57.3 | 179.0 | 0 | 0 | 0 |
| PS | 0.9 | 3.3 | 5.5 | 0 | 0 | 0 |
| SS | 0.9 | 3.6 | 22.5 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.9 | 86 |
| ContestWebServer0 | 3 | 180.9 | 137 |
| ContestWebServer1 | 3 | 188.5 | 136 |
| EvaluationService0 | 5 | 301.3 | 112 |
| LogService0 | 0 | 58.5 | 83 |
| ProxyService0 | 2 | 15.3 | 92 |
| ScoringService0 | 1 | 31.3 | 88 |
| Worker0 | 1 | 115.5 | 94 |
| Worker1 | 1 | 113.4 | 86 |
| Worker2 | 1 | 115.6 | 93 |
| Worker3 | 1 | 117.5 | 87 |
| Worker4 | 2 | 116.1 | 88 |
| Worker5 | 1 | 117.3 | 87 |
| Worker6 | 1 | 113.9 | 89 |
| Worker7 | 1 | 114.8 | 87 |

PostgreSQL backends for cmsdb: max 25 (max_connections 100); max 'active' 3; max lock waits 0; longest idle-in-transaction 0.9 s.

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
- EvaluationService-0: sweeper found 3 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 4 missed operations in total
- cms: sweeper found 7 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 170% max 744% (100% = 1 core); last mem 1.097GiB
- cmsload-fork-db-1: CPU mean 11% max 42% (100% = 1 core); last mem 106.8MiB
- cmsload-fork-driver-1: CPU mean 3% max 9% (100% = 1 core); last mem 1.145MiB
- cmsload-fork-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 31.47MiB
