# Run base-upstream-1

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791338539.46476.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 251 | 0 | 0.012 | 0.025 | 0.048 | 0.110 |
| contest_page | steady | 124 | 0 | 0.013 | 0.064 | 0.121 | 0.397 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.252 | 0.575 | 0.917 |
| home_logged | login_burst | 250 | 0 | 0.008 | 0.291 | 0.785 | 1.338 |
| login | login_burst | 250 | 0 | 0.261 | 0.780 | 1.027 | 1.420 |
| notifications | login_burst | 506 | 0 | 0.010 | 0.313 | 0.622 | 1.695 |
| notifications | start_burst | 499 | 0 | 0.011 | 0.049 | 0.054 | 0.211 |
| notifications | steady | 9485 | 0 | 0.012 | 0.059 | 0.091 | 0.352 |
| notifications | end_burst | 2499 | 0 | 0.012 | 0.024 | 0.048 | 0.116 |
| notifications | drain | 641 | 0 | 0.012 | 0.019 | 0.039 | 0.054 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.007 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.002 | 0.003 | 0.003 |
| rws_scores | steady | 569 | 0 | 0.002 | 0.005 | 0.008 | 0.023 |
| rws_scores | end_burst | 150 | 0 | 0.002 | 0.003 | 0.004 | 0.007 |
| rws_scores | drain | 48 | 0 | 0.002 | 0.004 | 0.004 | 0.004 |
| statement | start_burst | 430 | 0 | 0.022 | 0.049 | 0.082 | 0.241 |
| status_poll | start_burst | 144 | 0 | 0.013 | 0.045 | 0.052 | 0.070 |
| status_poll | steady | 3947 | 0 | 0.025 | 0.086 | 0.127 | 0.378 |
| status_poll | end_burst | 2443 | 0 | 0.015 | 0.057 | 0.074 | 0.145 |
| status_poll | drain | 459 | 0 | 0.015 | 0.052 | 0.065 | 0.170 |
| submission_details | start_burst | 14 | 0 | 0.036 | 0.082 | 0.128 | 0.140 |
| submission_details | steady | 413 | 0 | 0.029 | 0.107 | 0.147 | 0.227 |
| submission_details | end_burst | 201 | 0 | 0.034 | 0.049 | 0.076 | 0.082 |
| submission_details | drain | 68 | 0 | 0.034 | 0.043 | 0.112 | 0.123 |
| submissions_list | start_burst | 7 | 0 | 0.031 | 0.056 | 0.059 | 0.060 |
| submissions_list | steady | 125 | 0 | 0.034 | 0.121 | 0.137 | 0.148 |
| submissions_page | start_burst | 33 | 0 | 0.037 | 0.085 | 0.192 | 0.223 |
| submissions_page | steady | 787 | 0 | 0.037 | 0.118 | 0.167 | 0.337 |
| submissions_page | end_burst | 507 | 0 | 0.042 | 0.076 | 0.122 | 0.132 |
| submit | start_burst | 34 | 0 | 0.029 | 0.049 | 0.054 | 0.054 |
| submit | steady | 786 | 0 | 0.029 | 0.099 | 0.156 | 2.085 |
| submit | end_burst | 507 | 0 | 0.028 | 0.079 | 0.120 | 0.362 |
| task_description | start_burst | 618 | 0 | 0.012 | 0.027 | 0.042 | 0.220 |
| task_description | steady | 237 | 0 | 0.016 | 0.062 | 0.094 | 0.231 |
| task_description | end_burst | 165 | 0 | 0.015 | 0.030 | 0.040 | 0.069 |

All phases: login p50 0.261 s, p95 0.780 s; submit p50 0.029 s, p95 0.094 s; submit in end_burst p95 0.079 s.

Peak CWS request rate: 88 req/s (1 s buckets); mean 15.4 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1327; rejected by CWS: 0; DB rows: 1327.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 34 | 10.4 | 16.3 | 17.4 | 13.9 | 20.0 | 24.0 |
| steady | 786 | 10.7 | 22.1 | 39.1 | 13.4 | 26.3 | 55.7 |
| end_burst | 507 | 14.0 | 44.7 | 61.3 | 17.5 | 55.8 | 79.7 |

All: server submit->scored p50 11.5 s, p95 36.5 s, max 61.3 s.
Last submission scored 58 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'wa_zero': 71, 'wa_small': 146, 'ac': 362, 'wa_overflow': 175, 're': 114, 'tle': 184, 'ac_py': 136, 'ac_java': 54, 'ce': 85}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1327}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=584 p50 1.0 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 2105 operations (max 87 distinct entries); workers busy mean 3.0, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:60/1.6 +1:108/4.9 +2:88/5.2 +3:105/5.9 +4:85/4.2 +5:168/5.5 +6:56/4.9 +7:56/3.0 +8:164/4.0 +9:38/1.7 +10:83/0.9 +11:239/2.0 +12:45/2.5 +13:28/2.0 +14:43/1.8 +15:92/2.3 +16:163/1.8 +17:157/2.2 +18:50/2.5 +19:154/1.7 +20:110/1.9 +21:63/3.3 +22:156/5.3 +23:736/4.6 +24:1241/7.7 +25:2105/6.3 +26:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 7.3 | 13.0 | 0 | 0 | 0 |
| CWS0 | 0.9 | 62.1 | 224.2 | 0 | 0 | 0 |
| CWS1 | 0.9 | 39.4 | 490.5 | 3 | 0 | 0 |
| ES | 1.2 | 10.6 | 42.0 | 0 | 0 | 0 |
| PS | 1.0 | 7.5 | 15.9 | 0 | 0 | 0 |
| SS | 1.0 | 8.1 | 20.3 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.3 | 73 |
| ContestWebServer0 | 6 | 201.7 | 120 |
| ContestWebServer1 | 8 | 212.3 | 118 |
| EvaluationService0 | 5 | 179.1 | 72 |
| LogService0 | 0 | 76.6 | 66 |
| ProxyService0 | 3 | 13.7 | 77 |
| ScoringService0 | 1 | 33.8 | 72 |
| Worker0 | 1 | 117.3 | 80 |
| Worker1 | 1 | 111.7 | 75 |
| Worker2 | 1 | 113.0 | 73 |
| Worker3 | 1 | 117.3 | 79 |
| Worker4 | 1 | 109.8 | 72 |
| Worker5 | 2 | 121.2 | 72 |
| Worker6 | 1 | 119.6 | 72 |
| Worker7 | 1 | 128.1 | 72 |

PostgreSQL backends for cmsdb: max 30 (max_connections 100); max 'active' 4; max lock waits 0; longest idle-in-transaction 1.2 s.

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
- EvaluationService-0: sweeper found 6 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 6 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 2
- x2 COMMIT
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 170% max 694% (100% = 1 core); last mem 924.4MiB
- cmsload-upstream-db-1: CPU mean 10% max 39% (100% = 1 core); last mem 115.1MiB
- cmsload-upstream-driver-1: CPU mean 3% max 28% (100% = 1 core); last mem 1.195MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 27.32MiB
