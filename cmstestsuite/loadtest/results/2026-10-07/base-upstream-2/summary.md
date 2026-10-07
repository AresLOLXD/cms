# Run base-upstream-2

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791342295.579233.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 256 | 0 | 0.011 | 0.018 | 0.028 | 0.100 |
| contest_page | steady | 108 | 0 | 0.012 | 0.018 | 0.020 | 0.020 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.181 | 0.271 | 0.489 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.221 | 0.429 | 0.466 |
| login | login_burst | 250 | 0 | 0.217 | 0.574 | 0.712 | 1.002 |
| notifications | login_burst | 505 | 0 | 0.011 | 0.254 | 0.498 | 0.869 |
| notifications | start_burst | 500 | 0 | 0.010 | 0.021 | 0.052 | 0.056 |
| notifications | steady | 9493 | 0 | 0.011 | 0.017 | 0.029 | 0.105 |
| notifications | end_burst | 2498 | 0 | 0.012 | 0.025 | 0.049 | 0.367 |
| notifications | drain | 703 | 0 | 0.012 | 0.019 | 0.035 | 0.052 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.006 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.001 | 0.002 | 0.002 |
| rws_scores | steady | 570 | 0 | 0.002 | 0.002 | 0.003 | 0.007 |
| rws_scores | end_burst | 149 | 0 | 0.002 | 0.003 | 0.004 | 0.012 |
| rws_scores | drain | 53 | 0 | 0.002 | 0.004 | 0.004 | 0.005 |
| statement | start_burst | 438 | 0 | 0.017 | 0.029 | 0.060 | 0.136 |
| status_poll | start_burst | 152 | 0 | 0.012 | 0.049 | 0.061 | 0.115 |
| status_poll | steady | 3813 | 0 | 0.012 | 0.052 | 0.058 | 0.129 |
| status_poll | end_burst | 2712 | 0 | 0.014 | 0.057 | 0.144 | 0.358 |
| status_poll | drain | 568 | 0 | 0.015 | 0.049 | 0.061 | 0.117 |
| submission_details | start_burst | 10 | 0 | 0.027 | 0.060 | 0.077 | 0.081 |
| submission_details | steady | 395 | 0 | 0.023 | 0.041 | 0.071 | 0.133 |
| submission_details | end_burst | 162 | 0 | 0.034 | 0.058 | 0.101 | 0.206 |
| submission_details | drain | 79 | 0 | 0.033 | 0.043 | 0.056 | 0.066 |
| submissions_list | start_burst | 5 | 0 | 0.027 | 0.033 | 0.034 | 0.034 |
| submissions_list | steady | 114 | 0 | 0.027 | 0.045 | 0.065 | 0.071 |
| submissions_page | start_burst | 41 | 0 | 0.029 | 0.047 | 0.107 | 0.112 |
| submissions_page | steady | 787 | 0 | 0.027 | 0.046 | 0.061 | 0.139 |
| submissions_page | end_burst | 500 | 0 | 0.036 | 0.086 | 0.211 | 0.558 |
| submit | start_burst | 41 | 0 | 0.024 | 0.042 | 0.069 | 0.075 |
| submit | steady | 787 | 0 | 0.022 | 0.036 | 0.051 | 0.103 |
| submit | end_burst | 500 | 0 | 0.025 | 0.071 | 0.290 | 0.659 |
| task_description | start_burst | 639 | 0 | 0.010 | 0.017 | 0.045 | 0.124 |
| task_description | steady | 235 | 0 | 0.011 | 0.018 | 0.023 | 0.036 |
| task_description | end_burst | 167 | 0 | 0.013 | 0.033 | 0.222 | 0.323 |

All phases: login p50 0.217 s, p95 0.574 s; submit p50 0.023 s, p95 0.042 s; submit in end_burst p95 0.071 s.

Peak CWS request rate: 89 req/s (1 s buckets); mean 15.5 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1328; rejected by CWS: 0; DB rows: 1328.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 41 | 9.2 | 31.8 | 32.1 | 12.3 | 35.7 | 40.8 |
| steady | 787 | 9.4 | 22.3 | 40.8 | 12.4 | 28.8 | 57.3 |
| end_burst | 500 | 24.7 | 53.5 | 65.2 | 30.3 | 67.8 | 87.9 |

All: server submit->scored p50 11.3 s, p95 48.5 s, max 65.2 s.
Last submission scored 62 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ac': 373, 'ac_py': 131, 'wa_overflow': 196, 'ce': 94, 'tle': 183, 're': 101, 'wa_small': 133, 'wa_zero': 68, 'ac_java': 49}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1328}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=583 p50 1.0 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 2399 operations (max 100 distinct entries); workers busy mean 2.3, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:60/1.5 +1:87/3.4 +2:63/1.6 +3:73/2.4 +4:81/2.4 +5:0/0.3 +6:126/2.7 +7:138/3.3 +8:52/2.3 +9:51/1.1 +10:51/1.6 +11:77/2.0 +12:112/2.0 +13:60/2.0 +14:31/1.0 +15:310/3.8 +16:146/2.4 +17:230/2.8 +18:100/1.7 +19:64/2.4 +20:112/3.1 +21:800/3.8 +22:317/4.6 +23:64/2.3 +24:1988/5.8 +25:2399/7.2 +26:0/0.0 +27:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 1.8 | 6.0 | 0 | 0 | 0 |
| CWS0 | 0.9 | 50.7 | 157.4 | 0 | 0 | 0 |
| CWS1 | 0.9 | 29.5 | 209.2 | 0 | 0 | 0 |
| ES | 1.2 | 3.0 | 4.4 | 0 | 0 | 0 |
| PS | 0.9 | 1.5 | 4.3 | 0 | 0 | 0 |
| SS | 0.9 | 2.2 | 18.3 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.0 | 72 |
| ContestWebServer0 | 8 | 166.5 | 119 |
| ContestWebServer1 | 5 | 176.6 | 119 |
| EvaluationService0 | 5 | 143.7 | 72 |
| LogService0 | 0 | 66.3 | 66 |
| ProxyService0 | 2 | 11.7 | 77 |
| ScoringService0 | 1 | 28.6 | 73 |
| Worker0 | 2 | 99.6 | 72 |
| Worker1 | 1 | 100.4 | 74 |
| Worker2 | 1 | 91.9 | 74 |
| Worker3 | 1 | 100.7 | 72 |
| Worker4 | 1 | 105.3 | 72 |
| Worker5 | 2 | 106.3 | 77 |
| Worker6 | 1 | 117.9 | 79 |
| Worker7 | 1 | 107.2 | 82 |

PostgreSQL backends for cmsdb: max 28 (max_connections 100); max 'active' 5; max lock waits 0; longest idle-in-transaction 0.3 s.

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
- EvaluationService-0: sweeper found 1 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 1 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 1
- x1 [N] FATAL:  the database system is shutting down

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 135% max 769% (100% = 1 core); last mem 934.1MiB
- cmsload-upstream-db-1: CPU mean 9% max 72% (100% = 1 core); last mem 114.5MiB
- cmsload-upstream-driver-1: CPU mean 2% max 10% (100% = 1 core); last mem 1.184MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 27.49MiB
