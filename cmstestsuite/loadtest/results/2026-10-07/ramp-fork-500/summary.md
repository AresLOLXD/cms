# Run ramp-fork-500

Target fork, profile portable.
Users: 500 (loada 350, loadb 150). Contest 1500 s; start 1791349943.285039.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 503 | 0 | 0.011 | 0.028 | 0.072 | 0.138 |
| contest_page | steady | 264 | 0 | 0.018 | 0.028 | 0.040 | 0.055 |
| home_anon | login_burst | 500 | 0 | 0.006 | 0.007 | 0.015 | 0.225 |
| home_logged | login_burst | 500 | 0 | 0.007 | 0.009 | 0.013 | 0.065 |
| login | login_burst | 500 | 0 | 0.246 | 0.268 | 0.278 | 0.328 |
| notifications | login_burst | 997 | 0 | 0.008 | 0.010 | 0.012 | 0.074 |
| notifications | start_burst | 1000 | 0 | 0.009 | 0.020 | 0.026 | 0.087 |
| notifications | steady | 18985 | 0 | 0.015 | 0.024 | 0.037 | 0.338 |
| notifications | end_burst | 4994 | 0 | 0.018 | 0.047 | 0.281 | 0.862 |
| notifications | drain | 7297 | 0 | 0.014 | 0.024 | 0.040 | 0.269 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.009 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.002 | 0.002 | 0.002 |
| rws_scores | steady | 569 | 0 | 0.003 | 0.007 | 0.011 | 0.028 |
| rws_scores | end_burst | 149 | 0 | 0.006 | 0.013 | 0.033 | 0.101 |
| rws_scores | drain | 228 | 0 | 0.004 | 0.010 | 0.029 | 0.048 |
| statement | start_burst | 880 | 0 | 0.009 | 0.028 | 0.088 | 0.225 |
| status_poll | start_burst | 193 | 0 | 0.012 | 0.036 | 0.046 | 0.055 |
| status_poll | steady | 9309 | 0 | 0.016 | 0.052 | 0.068 | 0.225 |
| status_poll | end_burst | 7350 | 0 | 0.021 | 0.145 | 0.724 | 1.166 |
| status_poll | drain | 2901 | 0 | 0.022 | 0.065 | 0.102 | 0.332 |
| submission_details | start_burst | 8 | 0 | 0.032 | 0.055 | 0.058 | 0.059 |
| submission_details | steady | 823 | 0 | 0.044 | 0.060 | 0.123 | 0.225 |
| submission_details | end_burst | 255 | 0 | 0.052 | 0.100 | 0.501 | 1.132 |
| submission_details | drain | 273 | 0 | 0.045 | 0.064 | 0.134 | 0.208 |
| submissions_list | start_burst | 7 | 0 | 0.027 | 0.111 | 0.113 | 0.113 |
| submissions_list | steady | 253 | 0 | 0.045 | 0.062 | 0.146 | 0.167 |
| submissions_page | start_burst | 49 | 0 | 0.032 | 0.071 | 0.129 | 0.129 |
| submissions_page | steady | 1686 | 0 | 0.050 | 0.069 | 0.103 | 0.206 |
| submissions_page | end_burst | 1008 | 0 | 0.064 | 0.273 | 0.714 | 1.158 |
| submit | start_burst | 49 | 0 | 0.025 | 0.058 | 0.080 | 0.096 |
| submit | steady | 1686 | 0 | 0.031 | 0.050 | 0.085 | 0.375 |
| submit | end_burst | 1008 | 0 | 0.039 | 0.162 | 0.512 | 0.920 |
| task_description | start_burst | 1258 | 0 | 0.011 | 0.023 | 0.066 | 0.160 |
| task_description | steady | 495 | 0 | 0.019 | 0.029 | 0.050 | 0.144 |
| task_description | end_burst | 294 | 0 | 0.024 | 0.194 | 0.462 | 0.696 |

All phases: login p50 0.246 s, p95 0.268 s; submit p50 0.034 s, p95 0.072 s; submit in end_burst p95 0.162 s.

Peak CWS request rate: 195 req/s (1 s buckets); mean 31.2 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 2743; rejected by CWS: 0; DB rows: 2743.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 49 | 9.4 | 15.4 | 18.6 | 11.7 | 22.9 | 26.5 |
| steady | 1686 | 14.0 | 32.2 | 57.3 | 17.7 | 41.0 | 84.3 |
| end_burst | 1008 | 116.7 | 264.3 | 293.8 | 137.5 | 335.4 | 438.8 |

All: server submit->scored p50 19.6 s, p95 227.7 s, max 293.8 s.
Last submission scored 291 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 203, 're': 229, 'wa_small': 261, 'wa_overflow': 401, 'ac_java': 109, 'ac_py': 270, 'ac': 744, 'wa_zero': 157, 'tle': 369}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 2743}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 1400 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=1167 p50 1.1 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 11557 operations (max 478 distinct entries); workers busy mean 5.5, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:76/2.6 +1:180/5.3 +2:149/5.3 +3:524/4.1 +4:531/8.0 +5:304/6.1 +6:274/7.2 +7:367/6.2 +8:249/8.0 +9:197/7.9 +10:451/5.4 +11:231/7.7 +12:98/7.1 +13:279/6.5 +14:281/6.6 +15:249/8.0 +16:556/4.3 +17:358/7.1 +18:845/5.5 +19:161/3.2 +20:1455/8.0 +21:1678/8.0 +22:2657/8.0 +23:4752/8.0 +24:8520/8.0 +25:11557/8.0 +26:9382/8.0 +27:6757/8.0 +28:4332/8.0 +29:1982/6.2 +30:0/0.0 +31:0/0.0 +32:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 1.2 | 4.8 | 10.8 | 0 | 0 | 0 |
| CWS0 | 1.3 | 56.3 | 999.0 | 4 | 0 | 0 |
| CWS1 | 1.3 | 57.9 | 796.7 | 2 | 0 | 0 |
| CWS2 | 1.3 | 53.0 | 926.3 | 2 | 0 | 0 |
| CWS3 | 1.3 | 58.6 | 322.8 | 1 | 0 | 0 |
| ES | 1.6 | 37.6 | 205.9 | 0 | 0 | 0 |
| PS | 1.2 | 6.4 | 9.6 | 0 | 0 | 0 |
| SS | 1.3 | 18.8 | 30.2 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 7.2 | 87 |
| ContestWebServer0 | 3 | 267.8 | 140 |
| ContestWebServer1 | 3 | 277.4 | 137 |
| ContestWebServer2 | 3 | 270.9 | 137 |
| ContestWebServer3 | 3 | 267.1 | 137 |
| EvaluationService0 | 8 | 453.6 | 102 |
| LogService0 | 0 | 148.5 | 85 |
| ProxyService0 | 2 | 36.0 | 97 |
| ScoringService0 | 1 | 95.6 | 107 |
| Worker0 | 1 | 324.4 | 89 |
| Worker1 | 1 | 324.5 | 94 |
| Worker2 | 1 | 332.1 | 87 |
| Worker3 | 1 | 341.2 | 87 |
| Worker4 | 1 | 324.8 | 87 |
| Worker5 | 2 | 338.5 | 92 |
| Worker6 | 1 | 326.5 | 92 |
| Worker7 | 2 | 312.9 | 88 |

PostgreSQL backends for cmsdb: max 32 (max_connections 100); max 'active' 3; max lock waits 0; longest idle-in-transaction 1.2 s.

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
- EvaluationService-0: missed operation(s) x20
- ProxyService-0: missed operation(s) x7
- ScoringService-0: missed operation(s) x7
- cms: missed operation(s) x34
- EvaluationService-0: sweeper found 4 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 4 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 1
- x1 [N] FATAL:  database "cmsdb" does not exist

## Containers (docker stats, 5 s)

- cmsload-fork-cms-1: CPU mean 353% max 831% (100% = 1 core); last mem 1.384GiB
- cmsload-fork-db-1: CPU mean 20% max 81% (100% = 1 core); last mem 130.5MiB
- cmsload-fork-driver-1: CPU mean 7% max 26% (100% = 1 core); last mem 1.238MiB
- cmsload-fork-ranking-1: CPU mean 0% max 3% (100% = 1 core); last mem 34.27MiB
