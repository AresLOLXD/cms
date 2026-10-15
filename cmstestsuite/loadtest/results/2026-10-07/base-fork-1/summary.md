# Run base-fork-1

Target fork, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791336629.011405.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 253 | 0 | 0.011 | 0.015 | 0.028 | 0.045 |
| contest_page | steady | 117 | 0 | 0.011 | 0.023 | 0.028 | 0.032 |
| home_anon | login_burst | 250 | 0 | 0.006 | 0.008 | 0.041 | 0.226 |
| home_logged | login_burst | 250 | 0 | 0.007 | 0.009 | 0.010 | 0.063 |
| login | login_burst | 250 | 0 | 0.260 | 0.268 | 0.271 | 0.329 |
| notifications | login_burst | 509 | 0 | 0.009 | 0.011 | 0.012 | 0.013 |
| notifications | start_burst | 499 | 0 | 0.009 | 0.016 | 0.021 | 0.106 |
| notifications | steady | 9495 | 0 | 0.010 | 0.019 | 0.030 | 0.141 |
| notifications | end_burst | 2496 | 0 | 0.014 | 0.031 | 0.095 | 0.645 |
| notifications | drain | 775 | 0 | 0.012 | 0.019 | 0.030 | 0.131 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.002 | 0.002 | 0.002 |
| rws_scores | steady | 569 | 0 | 0.002 | 0.003 | 0.004 | 0.006 |
| rws_scores | end_burst | 150 | 0 | 0.003 | 0.005 | 0.007 | 0.008 |
| rws_scores | drain | 56 | 0 | 0.003 | 0.005 | 0.006 | 0.006 |
| statement | start_burst | 440 | 0 | 0.009 | 0.024 | 0.060 | 0.103 |
| status_poll | start_burst | 115 | 0 | 0.010 | 0.028 | 0.044 | 0.061 |
| status_poll | steady | 4099 | 0 | 0.011 | 0.044 | 0.056 | 0.190 |
| status_poll | end_burst | 2540 | 0 | 0.016 | 0.074 | 0.268 | 0.669 |
| status_poll | drain | 501 | 0 | 0.016 | 0.052 | 0.071 | 0.097 |
| submission_details | start_burst | 8 | 0 | 0.030 | 0.042 | 0.043 | 0.043 |
| submission_details | steady | 415 | 0 | 0.029 | 0.049 | 0.076 | 0.095 |
| submission_details | end_burst | 195 | 0 | 0.043 | 0.082 | 0.151 | 0.481 |
| submission_details | drain | 49 | 0 | 0.041 | 0.051 | 0.052 | 0.052 |
| submissions_list | start_burst | 4 | 0 | 0.041 | 0.091 | 0.097 | 0.099 |
| submissions_list | steady | 120 | 0 | 0.028 | 0.053 | 0.057 | 0.057 |
| submissions_page | start_burst | 28 | 0 | 0.030 | 0.052 | 0.094 | 0.110 |
| submissions_page | steady | 810 | 0 | 0.032 | 0.058 | 0.096 | 0.199 |
| submissions_page | end_burst | 501 | 0 | 0.051 | 0.110 | 0.207 | 0.684 |
| submit | start_burst | 28 | 0 | 0.028 | 0.051 | 0.057 | 0.058 |
| submit | steady | 810 | 0 | 0.023 | 0.042 | 0.064 | 0.257 |
| submit | end_burst | 501 | 0 | 0.031 | 0.094 | 0.212 | 0.454 |
| task_description | start_burst | 623 | 0 | 0.010 | 0.017 | 0.040 | 0.095 |
| task_description | steady | 226 | 0 | 0.012 | 0.024 | 0.080 | 0.125 |
| task_description | end_burst | 150 | 0 | 0.018 | 0.100 | 0.157 | 0.433 |

All phases: login p50 0.260 s, p95 0.268 s; submit p50 0.025 s, p95 0.055 s; submit in end_burst p95 0.094 s.

Peak CWS request rate: 90 req/s (1 s buckets); mean 15.5 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1339; rejected by CWS: 0; DB rows: 1339.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 28 | 10.7 | 22.2 | 24.4 | 13.9 | 26.2 | 30.9 |
| steady | 810 | 10.3 | 22.2 | 46.3 | 13.7 | 28.9 | 56.1 |
| end_burst | 501 | 16.5 | 49.9 | 64.3 | 20.7 | 66.1 | 96.2 |

All: server submit->scored p50 12.1 s, p95 40.3 s, max 64.3 s.
Last submission scored 60 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ac': 374, 'wa_zero': 64, 'wa_small': 145, 'wa_overflow': 198, 'ac_py': 138, 'tle': 177, 'ce': 90, 're': 106, 'ac_java': 47}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1339}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=598 p50 1.0 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 1674 operations (max 72 distinct entries); workers busy mean 3.0, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:43/1.4 +1:129/4.2 +2:49/2.2 +3:77/2.0 +4:85/2.8 +5:48/2.9 +6:72/2.5 +7:112/3.1 +8:42/2.4 +9:136/3.1 +10:118/2.4 +11:44/2.6 +12:88/3.4 +13:90/2.1 +14:44/3.2 +15:74/3.2 +16:199/2.5 +17:84/2.8 +18:187/2.3 +19:72/3.8 +20:136/5.0 +21:364/5.0 +22:244/6.7 +23:98/5.7 +24:1000/7.5 +25:1674/7.5 +26:0/0.0 +27:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 3.4 | 4.6 | 0 | 0 | 0 |
| CWS0 | 0.9 | 35.9 | 184.6 | 0 | 0 | 0 |
| CWS1 | 0.9 | 64.7 | 164.7 | 0 | 0 | 0 |
| ES | 1.1 | 36.2 | 134.1 | 0 | 0 | 0 |
| PS | 0.9 | 3.9 | 7.9 | 0 | 0 | 0 |
| SS | 0.9 | 10.7 | 24.2 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 5.2 | 87 |
| ContestWebServer0 | 3 | 189.9 | 136 |
| ContestWebServer1 | 3 | 196.9 | 136 |
| EvaluationService0 | 8 | 210.7 | 93 |
| LogService0 | 0 | 68.1 | 85 |
| ProxyService0 | 3 | 15.3 | 93 |
| ScoringService0 | 1 | 38.8 | 93 |
| Worker0 | 1 | 130.3 | 90 |
| Worker1 | 1 | 150.0 | 95 |
| Worker2 | 1 | 139.4 | 87 |
| Worker3 | 2 | 141.1 | 87 |
| Worker4 | 2 | 148.4 | 87 |
| Worker5 | 1 | 143.7 | 96 |
| Worker6 | 1 | 144.2 | 88 |
| Worker7 | 2 | 155.0 | 91 |

PostgreSQL backends for cmsdb: max 26 (max_connections 100); max 'active' 3; max lock waits 1; longest idle-in-transaction 1.2 s.

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
- EvaluationService-0: sweeper found 3 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 3 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 1
- x1 [N] FATAL:  the database system is starting up

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 209% max 815% (100% = 1 core); last mem 1.091GiB
- cmsload-fork-db-1: CPU mean 11% max 89% (100% = 1 core); last mem 107.3MiB
- cmsload-fork-driver-1: CPU mean 3% max 16% (100% = 1 core); last mem 1.137MiB
- cmsload-fork-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 28.5MiB
