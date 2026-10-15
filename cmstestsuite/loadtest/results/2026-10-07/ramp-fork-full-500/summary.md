# Run ramp-fork-full-500

Target fork, profile full.
Users: 500 (loada 350, loadb 150). Contest 1500 s; start 1791357754.934619.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 509 | 0 | 0.009 | 0.013 | 0.031 | 0.080 |
| contest_page | steady | 231 | 0 | 0.011 | 0.020 | 0.026 | 0.046 |
| home_anon | login_burst | 500 | 0 | 0.005 | 0.007 | 0.009 | 0.185 |
| home_logged | login_burst | 500 | 0 | 0.006 | 0.007 | 0.012 | 0.193 |
| login | login_burst | 500 | 0 | 0.215 | 0.220 | 0.225 | 0.267 |
| notifications | login_burst | 997 | 0 | 0.007 | 0.011 | 0.012 | 0.023 |
| notifications | start_burst | 1000 | 0 | 0.008 | 0.014 | 0.018 | 0.051 |
| notifications | steady | 18992 | 0 | 0.009 | 0.017 | 0.023 | 0.251 |
| notifications | end_burst | 4995 | 0 | 0.013 | 0.022 | 0.048 | 0.713 |
| notifications | drain | 1848 | 0 | 0.011 | 0.016 | 0.022 | 0.119 |
| rws_scores | login_burst | 188 | 0 | 0.025 | 0.042 | 0.042 | 0.043 |
| rws_scores | start_burst | 60 | 0 | 0.021 | 0.042 | 0.043 | 0.043 |
| rws_scores | steady | 1132 | 0 | 0.002 | 0.041 | 0.043 | 0.044 |
| rws_scores | end_burst | 300 | 0 | 0.003 | 0.006 | 0.012 | 0.095 |
| rws_scores | drain | 130 | 0 | 0.003 | 0.006 | 0.010 | 0.029 |
| statement | start_burst | 910 | 0 | 0.007 | 0.010 | 0.043 | 0.137 |
| status_poll | start_burst | 218 | 0 | 0.009 | 0.024 | 0.032 | 0.037 |
| status_poll | steady | 9630 | 0 | 0.010 | 0.036 | 0.048 | 0.285 |
| status_poll | end_burst | 5542 | 0 | 0.014 | 0.049 | 0.109 | 0.725 |
| status_poll | drain | 1144 | 0 | 0.015 | 0.046 | 0.056 | 0.167 |
| submission_details | start_burst | 9 | 0 | 0.022 | 0.039 | 0.042 | 0.042 |
| submission_details | steady | 785 | 0 | 0.023 | 0.042 | 0.050 | 0.175 |
| submission_details | end_burst | 369 | 0 | 0.035 | 0.054 | 0.141 | 0.173 |
| submission_details | drain | 132 | 0 | 0.033 | 0.042 | 0.049 | 0.050 |
| submissions_list | start_burst | 7 | 0 | 0.025 | 0.041 | 0.044 | 0.045 |
| submissions_list | steady | 249 | 0 | 0.025 | 0.048 | 0.053 | 0.171 |
| submissions_page | start_burst | 47 | 0 | 0.024 | 0.092 | 0.102 | 0.104 |
| submissions_page | steady | 1610 | 0 | 0.026 | 0.050 | 0.072 | 0.163 |
| submissions_page | end_burst | 1009 | 0 | 0.043 | 0.072 | 0.149 | 0.254 |
| submit | start_burst | 47 | 0 | 0.020 | 0.050 | 0.052 | 0.053 |
| submit | steady | 1610 | 0 | 0.020 | 0.036 | 0.054 | 0.476 |
| submit | end_burst | 1009 | 0 | 0.027 | 0.063 | 0.141 | 0.840 |
| task_description | start_burst | 1296 | 0 | 0.008 | 0.012 | 0.048 | 0.104 |
| task_description | steady | 483 | 0 | 0.011 | 0.021 | 0.025 | 0.066 |
| task_description | end_burst | 304 | 0 | 0.016 | 0.034 | 0.099 | 0.138 |

All phases: login p50 0.215 s, p95 0.220 s; submit p50 0.023 s, p95 0.043 s; submit in end_burst p95 0.063 s.

Peak CWS request rate: 191 req/s (1 s buckets); mean 32.0 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 2666; rejected by CWS: 0; DB rows: 2666.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 47 | 21.9 | 42.0 | 56.5 | 26.1 | 56.6 | 64.0 |
| steady | 1610 | 16.3 | 42.3 | 70.6 | 20.9 | 53.6 | 95.3 |
| end_burst | 1009 | 20.0 | 67.5 | 85.0 | 24.9 | 83.9 | 112.6 |

All: server submit->scored p50 17.2 s, p95 55.5 s, max 85.0 s.
Last submission scored 82 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 196, 'ac': 769, 'wa_zero': 128, 'wa_small': 239, 'tle': 358, 'ac_py': 263, 'wa_overflow': 395, 're': 209, 'ac_java': 109}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 2666}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada, loadb): 1850 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=1577 p50 1.1 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 950 operations (max 176 distinct entries); workers busy mean 3.6, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:33/1.2 +1:67/4.7 +2:45/3.8 +3:70/3.5 +4:38/3.2 +5:36/3.6 +6:50/3.6 +7:53/3.2 +8:48/3.0 +9:56/4.2 +10:73/3.4 +11:40/3.5 +12:43/3.4 +13:40/3.5 +14:54/2.8 +15:43/3.6 +16:54/4.3 +17:37/2.7 +18:29/2.7 +19:56/3.1 +20:42/5.9 +21:57/5.2 +22:71/8.0 +23:81/8.0 +24:497/8.0 +25:950/8.0 +26:45/1.3 +27:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 2.2 | 6.2 | 0 | 0 | 0 |
| CWS0 | 0.9 | 34.4 | 126.1 | 0 | 0 | 0 |
| CWS1 | 0.9 | 31.6 | 142.0 | 0 | 0 | 0 |
| CWS2 | 0.9 | 34.6 | 58.4 | 0 | 0 | 0 |
| CWS3 | 0.9 | 30.9 | 172.9 | 0 | 0 | 0 |
| ES | 1.3 | 61.1 | 127.7 | 0 | 0 | 0 |
| PS | 1.0 | 2.9 | 5.6 | 0 | 0 | 0 |
| SS | 1.0 | 6.3 | 46.4 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 5.0 | 86 |
| ContestWebServer0 | 3 | 177.5 | 136 |
| ContestWebServer1 | 3 | 180.3 | 135 |
| ContestWebServer2 | 3 | 175.4 | 136 |
| ContestWebServer3 | 3 | 175.2 | 135 |
| EvaluationService0 | 9 | 514.7 | 113 |
| LogService0 | 0 | 94.6 | 85 |
| ProxyService0 | 3 | 27.0 | 94 |
| ScoringService0 | 1 | 54.3 | 89 |
| Worker0 | 2 | 193.8 | 89 |
| Worker1 | 1 | 191.4 | 88 |
| Worker2 | 1 | 191.2 | 88 |
| Worker3 | 1 | 193.1 | 89 |
| Worker4 | 1 | 194.8 | 90 |
| Worker5 | 1 | 193.3 | 87 |
| Worker6 | 1 | 191.6 | 93 |
| Worker7 | 1 | 192.1 | 87 |

PostgreSQL backends for cmsdb: max 33 (max_connections 100); max 'active' 3; max lock waits 0; longest idle-in-transaction 4.9 s.

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
- EvaluationService-0: missed operation(s) x17
- ProxyService-0: missed operation(s) x6
- ScoringService-0: missed operation(s) x6
- cms: missed operation(s) x29
- EvaluationService-0: sweeper found 4 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 4 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 1
- x1 [N] FATAL:  the database system is shutting down

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 268% max 787% (100% = 1 core); last mem 1.337GiB
- cmsload-fork-db-1: CPU mean 17% max 72% (100% = 1 core); last mem 133.2MiB
- cmsload-fork-driver-1: CPU mean 5% max 19% (100% = 1 core); last mem 1.297MiB
- cmsload-fork-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 39MiB
