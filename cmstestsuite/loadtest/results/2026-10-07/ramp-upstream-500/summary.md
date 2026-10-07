# Run ramp-upstream-500

Target upstream, profile portable.
Users: 500 (loada 350, loadb 150). Contest 1500 s; start 1791355639.721439.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 506 | 0 | 0.010 | 0.016 | 0.036 | 0.117 |
| contest_page | steady | 246 | 0 | 0.013 | 0.019 | 0.022 | 0.024 |
| home_anon | login_burst | 500 | 0 | 0.006 | 0.159 | 0.224 | 0.357 |
| home_logged | login_burst | 500 | 0 | 0.007 | 0.181 | 0.374 | 0.662 |
| login | login_burst | 500 | 0 | 0.218 | 0.401 | 0.614 | 1.028 |
| notifications | login_burst | 995 | 0 | 0.009 | 0.172 | 0.428 | 0.977 |
| notifications | start_burst | 1000 | 0 | 0.010 | 0.019 | 0.050 | 0.158 |
| notifications | steady | 18986 | 0 | 0.012 | 0.018 | 0.027 | 0.148 |
| notifications | end_burst | 4999 | 0 | 0.014 | 0.023 | 0.046 | 0.152 |
| notifications | drain | 4061 | 0 | 0.012 | 0.017 | 0.027 | 0.104 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.009 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.002 | 0.002 | 0.003 |
| rws_scores | steady | 569 | 0 | 0.002 | 0.004 | 0.010 | 0.037 |
| rws_scores | end_burst | 150 | 0 | 0.004 | 0.006 | 0.008 | 0.012 |
| rws_scores | drain | 131 | 0 | 0.004 | 0.006 | 0.011 | 0.013 |
| statement | start_burst | 880 | 0 | 0.018 | 0.033 | 0.065 | 0.163 |
| status_poll | start_burst | 290 | 0 | 0.013 | 0.044 | 0.063 | 0.085 |
| status_poll | steady | 8803 | 0 | 0.013 | 0.044 | 0.055 | 0.187 |
| status_poll | end_burst | 5737 | 0 | 0.016 | 0.053 | 0.073 | 0.215 |
| status_poll | drain | 1738 | 0 | 0.016 | 0.050 | 0.064 | 0.120 |
| submission_details | start_burst | 31 | 0 | 0.034 | 0.044 | 0.153 | 0.199 |
| submission_details | steady | 815 | 0 | 0.033 | 0.044 | 0.070 | 0.137 |
| submission_details | end_burst | 316 | 0 | 0.037 | 0.057 | 0.088 | 0.098 |
| submission_details | drain | 193 | 0 | 0.035 | 0.045 | 0.072 | 0.108 |
| submissions_list | start_burst | 12 | 0 | 0.027 | 0.076 | 0.100 | 0.106 |
| submissions_list | steady | 220 | 0 | 0.034 | 0.046 | 0.053 | 0.068 |
| submissions_page | start_burst | 71 | 0 | 0.039 | 0.055 | 0.118 | 0.121 |
| submissions_page | steady | 1622 | 0 | 0.037 | 0.052 | 0.072 | 0.131 |
| submissions_page | end_burst | 989 | 0 | 0.048 | 0.077 | 0.119 | 0.211 |
| submit | start_burst | 71 | 0 | 0.028 | 0.045 | 0.078 | 0.141 |
| submit | steady | 1622 | 0 | 0.025 | 0.040 | 0.060 | 0.691 |
| submit | end_burst | 989 | 0 | 0.030 | 0.055 | 0.096 | 0.282 |
| task_description | start_burst | 1275 | 0 | 0.010 | 0.018 | 0.055 | 0.113 |
| task_description | steady | 501 | 0 | 0.014 | 0.020 | 0.024 | 0.044 |
| task_description | end_burst | 286 | 0 | 0.017 | 0.040 | 0.070 | 0.111 |

All phases: login p50 0.218 s, p95 0.401 s; submit p50 0.027 s, p95 0.046 s; submit in end_burst p95 0.055 s.

Peak CWS request rate: 187 req/s (1 s buckets); mean 31.0 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 2682; rejected by CWS: 0; DB rows: 2682.

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 71 | 7.6 | 18.5 | 35.3 | 8.8 | 24.2 | 47.1 |
| steady | 1622 | 12.6 | 34.1 | 49.9 | 15.2 | 42.0 | 66.8 |
| end_burst | 989 | 29.5 | 150.9 | 170.2 | 37.2 | 179.6 | 245.7 |

All: server submit->scored p50 16.3 s, p95 109.0 s, max 170.2 s.
Last submission scored 167 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 0
Score mismatches vs expected: 0
Mix: {'ce': 200, 'ac': 739, 're': 200, 'wa_overflow': 371, 'tle': 373, 'wa_small': 275, 'wa_zero': 150, 'ac_py': 265, 'ac_java': 109}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 2682}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 1400 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=1150 p50 1.1 p95 1.9 max 2.0 s

## Internals (monitor, 2 s samples)

ES queue: max 7441 operations (max 309 distinct entries); workers busy mean 4.6, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:91/3.7 +1:845/3.4 +2:214/5.5 +3:182/4.7 +4:878/3.6 +5:762/6.9 +6:309/4.9 +7:179/6.2 +8:404/4.5 +9:141/4.7 +10:876/2.7 +11:665/6.3 +12:282/3.4 +13:506/4.7 +14:895/2.9 +15:357/6.6 +16:604/3.9 +17:778/3.9 +18:312/5.9 +19:234/4.6 +20:229/6.0 +21:1014/6.9 +22:664/7.9 +23:1881/8.0 +24:5339/8.0 +25:7441/8.0 +26:4766/8.0 +27:1766/5.6 +28:0/0.0 +29:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 0.9 | 2.3 | 7.0 | 0 | 0 | 0 |
| CWS0 | 0.9 | 16.9 | 184.2 | 0 | 0 | 0 |
| CWS1 | 0.9 | 16.1 | 200.3 | 0 | 0 | 0 |
| CWS2 | 0.9 | 16.4 | 196.1 | 0 | 0 | 0 |
| CWS3 | 0.9 | 25.0 | 183.6 | 0 | 0 | 0 |
| ES | 1.2 | 4.2 | 75.0 | 0 | 0 | 0 |
| PS | 0.9 | 2.6 | 5.9 | 0 | 0 | 0 |
| SS | 1.0 | 8.4 | 19.0 | 0 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 4.4 | 73 |
| ContestWebServer0 | 5 | 203.9 | 122 |
| ContestWebServer1 | 5 | 207.2 | 123 |
| ContestWebServer2 | 5 | 205.3 | 123 |
| ContestWebServer3 | 7 | 208.4 | 123 |
| EvaluationService0 | 15 | 295.5 | 75 |
| LogService0 | 0 | 132.3 | 66 |
| ProxyService0 | 2 | 25.4 | 79 |
| ScoringService0 | 1 | 65.8 | 72 |
| Worker0 | 2 | 231.3 | 74 |
| Worker1 | 1 | 223.4 | 73 |
| Worker2 | 1 | 214.7 | 73 |
| Worker3 | 1 | 221.5 | 79 |
| Worker4 | 1 | 221.3 | 79 |
| Worker5 | 1 | 235.7 | 73 |
| Worker6 | 1 | 220.4 | 73 |
| Worker7 | 1 | 222.3 | 78 |

PostgreSQL backends for cmsdb: max 49 (max_connections 100); max 'active' 3; max lock waits 0; longest idle-in-transaction 3.0 s.

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
- EvaluationService-0: missed operation(s) x18
- ProxyService-0: missed operation(s) x6
- ScoringService-0: missed operation(s) x7
- cms: missed operation(s) x31
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

- cmsload-upstream-cms-1: CPU mean 275% max 800% (100% = 1 core); last mem 1.123GiB
- cmsload-upstream-db-1: CPU mean 16% max 90% (100% = 1 core); last mem 147.6MiB
- cmsload-upstream-driver-1: CPU mean 5% max 18% (100% = 1 core); last mem 1.227MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 2% (100% = 1 core); last mem 32.79MiB
