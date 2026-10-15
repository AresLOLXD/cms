# Run ci-4-upstream

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791394794.728712.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 251 | 0 | 0.014 | 0.079 | 0.238 | 0.298 |
| contest_page | steady | 89 | 0 | 0.140 | 1.871 | 3.321 | 5.584 |
| home_anon | login_burst | 250 | 0 | 0.008 | 0.593 | 1.386 | 2.584 |
| home_logged | login_burst | 250 | 0 | 0.010 | 0.718 | 2.593 | 2.932 |
| login | login_burst | 250 | 0 | 0.318 | 1.524 | 2.289 | 2.893 |
| notifications | login_burst | 496 | 0 | 0.012 | 0.664 | 0.945 | 2.460 |
| notifications | start_burst | 500 | 0 | 0.015 | 0.190 | 0.659 | 1.353 |
| notifications | steady | 9279 | 0 | 0.241 | 2.648 | 4.731 | 9.267 |
| notifications | end_burst | 2374 | 92 | 1.161 | 14.224 | 60.163 | 60.862 |
| notifications | drain | 7815 | 49 | 0.074 | 2.276 | 50.925 | 63.486 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.004 | 0.007 | 0.008 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.014 | 0.025 | 0.059 |
| rws_scores | end_burst | 149 | 0 | 0.003 | 0.013 | 0.025 | 0.038 |
| rws_scores | drain | 510 | 0 | 0.003 | 0.014 | 0.036 | 0.074 |
| statement | start_burst | 428 | 0 | 0.025 | 0.118 | 0.372 | 0.581 |
| status_poll | start_burst | 121 | 0 | 0.047 | 0.509 | 1.181 | 1.284 |
| status_poll | steady | 5917 | 0 | 0.175 | 2.620 | 4.516 | 9.448 |
| status_poll | end_burst | 3139 | 112 | 1.502 | 25.170 | 60.117 | 61.162 |
| status_poll | drain | 2178 | 70 | 0.268 | 54.517 | 60.127 | 63.225 |
| submission_details | start_burst | 8 | 0 | 0.101 | 0.801 | 0.926 | 0.957 |
| submission_details | steady | 253 | 0 | 0.261 | 2.775 | 4.323 | 5.281 |
| submission_details | end_burst | 76 | 0 | 1.650 | 7.922 | 22.396 | 52.184 |
| submission_details | drain | 177 | 0 | 0.190 | 1.908 | 17.626 | 38.012 |
| submissions_list | start_burst | 6 | 0 | 0.120 | 0.262 | 0.275 | 0.278 |
| submissions_list | steady | 90 | 0 | 0.339 | 4.364 | 7.628 | 8.124 |
| submissions_page | start_burst | 28 | 0 | 0.044 | 0.394 | 0.965 | 1.173 |
| submissions_page | steady | 588 | 0 | 0.244 | 2.932 | 5.113 | 6.872 |
| submissions_page | end_burst | 404 | 20 | 2.003 | 53.642 | 60.155 | 61.150 |
| submissions_page | drain | 23 | 1 | 0.747 | 59.888 | 60.111 | 60.148 |
| submit | start_burst | 28 | 0 | 0.036 | 0.278 | 0.404 | 0.443 |
| submit | steady | 588 | 0 | 0.250 | 4.180 | 6.917 | 11.121 |
| submit | end_burst | 464 | 30 | 3.718 | 60.578 | 112.652 | 114.592 |
| submit | drain | 15 | 0 | 0.157 | 57.546 | 57.692 | 57.729 |
| task_description | start_burst | 631 | 0 | 0.013 | 0.089 | 0.287 | 0.450 |
| task_description | steady | 175 | 0 | 0.147 | 2.705 | 4.361 | 5.147 |
| task_description | end_burst | 142 | 11 | 1.621 | 60.018 | 60.054 | 60.424 |

All phases: login p50 0.318 s, p95 1.524 s; submit p50 0.639 s, p95 58.414 s; submit in end_burst p95 60.578 s.

Failed requests by (kind, status, error):

- status_poll status=500 : 182
- notifications status=500 : 141
- submit status=500 : 30
- submissions_page status=500 : 21
- task_description status=500 : 11

Peak CWS request rate: 92 req/s (1 s buckets); mean 14.2 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1095; rejected by CWS: 52; DB rows: 1043.
  - rejected b047 sumab status=302 location=../../../loadb/tasks/sumab/submissions
  - rejected b050 mulb status=302 location=../../../loadb/tasks/mulb/submissions
  - rejected a073 suma status=302 location=../../../loada/tasks/suma/submissions
  - rejected a105 libre status=302 location=../../../loada
  - rejected b022 sumab status=500 location=None
  - rejected a133 umbral status=302 location=../../../loada
  - rejected a141 libre status=500 location=None
  - rejected a050 suma status=302 location=../../../loada/tasks/suma/submissions
  - rejected b056 sumab status=302 location=../../../loadb
  - rejected a153 suma status=500 location=None

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 28 | 16.2 | 31.3 | 32.1 | 20.0 | 43.0 | 46.0 |
| steady | 588 | 142.4 | 170.8 | 189.2 | 168.2 | 226.2 | 267.1 |
| end_burst | 406 | 378.1 | 659.0 | 702.3 | 452.8 | 790.1 | 1004.6 |

All: server submit->scored p50 156.5 s, p95 596.5 s, max 702.3 s.
Last submission scored 699 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 21
Score mismatches vs expected: 0
Mix: {'ac': 321, 'wa_zero': 65, 'ac_py': 120, 'ce': 79, 'ac_java': 43, 'wa_small': 104, 'tle': 144, 'wa_overflow': 130, 're': 89}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1043}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=497 p50 1.2 p95 2.0 max 2.4 s

## Internals (monitor, 2 s samples)

ES queue: max 6966 operations (max 296 distinct entries); workers busy mean 6.2, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:58/2.8 +1:174/6.9 +2:210/7.9 +3:490/8.0 +4:762/8.0 +5:879/8.0 +6:946/8.0 +7:1091/7.9 +8:1027/7.9 +9:923/8.0 +10:1118/7.9 +11:1196/8.0 +12:1100/8.0 +13:1070/7.9 +14:1146/7.8 +15:1191/7.9 +16:1417/8.0 +17:1460/7.9 +18:1299/7.9 +19:1056/8.0 +20:1229/7.9 +21:1651/7.9 +22:2399/7.9 +23:4328/7.9 +24:6033/7.9 +25:6966/8.0 +26:6900/7.9 +27:6456/8.0 +28:5905/8.0 +29:5105/8.0 +30:4355/8.0 +31:3805/7.9 +32:3155/8.0 +33:2480/7.9 +34:1705/7.9 +35:1005/7.9 +36:305/4.8 +37:0/0.0 +38:0/0.0 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 3.2 | 69.9 | 609.4 | 4 | 0 | 0 |
| CWS0 | 8.4 | 650.3 | 1593.2 | 100 | 4 | 0 |
| CWS1 | 7.4 | 592.2 | 3376.5 | 113 | 4 | 0 |
| ES | 5.0 | 377.1 | 2643.8 | 24 | 4 | 0 |
| PS | 3.6 | 83.9 | 621.9 | 1 | 0 | 0 |
| SS | 3.6 | 145.3 | 559.7 | 6 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 6.8 | 73 |
| ContestWebServer0 | 15 | 306.2 | 146 |
| ContestWebServer1 | 16 | 324.2 | 128 |
| EvaluationService0 | 10 | 164.0 | 77 |
| LogService0 | 0 | 76.1 | 66 |
| ProxyService0 | 5 | 17.2 | 78 |
| ScoringService0 | 1 | 34.0 | 74 |
| Worker0 | 1 | 81.8 | 72 |
| Worker1 | 2 | 90.6 | 79 |
| Worker2 | 2 | 83.2 | 73 |
| Worker3 | 1 | 85.4 | 73 |
| Worker4 | 1 | 88.1 | 74 |
| Worker5 | 1 | 80.6 | 80 |
| Worker6 | 2 | 80.3 | 80 |
| Worker7 | 2 | 82.2 | 75 |

PostgreSQL backends for cmsdb: max 56 (max_connections 100); max 'active' 6; max lock waits 0; longest idle-in-transaction 60.4 s.

## Service logs

Log files: 16

| service | WARNING | ERROR | CRITICAL |
|---|---|---|---|
| AdminWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-0 | 0 | 1162 | 0 |
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
| cms | 0 | 1162 | 0 |

Pattern hits:
- ContestWebServer-0: QueuePool x1162
- ContestWebServer-0: TimeoutError x1925
- ContestWebServer-0: Traceback x770
- ContestWebServer-0: timeout/timed out x1932
- EvaluationService-0: missed operation(s) x25
- ProxyService-0: missed operation(s) x8
- ScoringService-0: missed operation(s) x9
- cms: QueuePool x1162
- cms: TimeoutError x1925
- cms: Traceback x770
- cms: missed operation(s) x42
- cms: timeout/timed out x1932
- EvaluationService-0: sweeper found 6 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 6 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):
- x385 ContestWebServer-0 ERROR: `2026-10-07 18:05:11,767 - ERROR [Contest,0 8162 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x385 cms ERROR: `2026-10-07 18:05:11,767 - ERROR [Contest,0 8162 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x95 ContestWebServer-0 ERROR: `2026-10-07 18:05:14,102 - ERROR [Contest,0 8487 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.3)`
- x95 ContestWebServer-0 ERROR: `2026-10-07 18:05:14,133 - ERROR [Contest,0 8487 web::log_request] 500 GET /loada/notifications (172.18.0.3) 60044.31ms`
- x95 cms ERROR: `2026-10-07 18:05:14,102 - ERROR [Contest,0 8487 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.3)`
- x95 cms ERROR: `2026-10-07 18:05:14,133 - ERROR [Contest,0 8487 web::log_request] 500 GET /loada/notifications (172.18.0.3) 60044.31ms`
- x46 ContestWebServer-0 ERROR: `2026-10-07 18:05:18,007 - ERROR [Contest,0 7997 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.3)`
- x46 ContestWebServer-0 ERROR: `2026-10-07 18:05:18,010 - ERROR [Contest,0 7997 web::log_request] 500 GET /loadb/notifications (172.18.0.3) 60011.79ms`
- x46 cms ERROR: `2026-10-07 18:05:18,007 - ERROR [Contest,0 7997 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.3)`
- x46 cms ERROR: `2026-10-07 18:05:18,010 - ERROR [Contest,0 7997 web::log_request] 500 GET /loadb/notifications (172.18.0.3) 60011.79ms`
- x36 ContestWebServer-0 ERROR: `2026-10-07 18:05:19,056 - ERROR [Contest,0 8210 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/7043948659883423302 (172.18.0.3)`
- x36 ContestWebServer-0 ERROR: `2026-10-07 18:05:19,063 - ERROR [Contest,0 8210 web::log_request] 500 GET /loada/tasks/suma/submissions/7043948659883423302 (172.18.0.3) 60077.24ms`
- x36 cms ERROR: `2026-10-07 18:05:19,056 - ERROR [Contest,0 8210 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/7043948659883423302 (172.18.0.3)`
- x36 cms ERROR: `2026-10-07 18:05:19,063 - ERROR [Contest,0 8210 web::log_request] 500 GET /loada/tasks/suma/submissions/7043948659883423302 (172.18.0.3) 60077.24ms`
- x34 ContestWebServer-0 ERROR: `2026-10-07 18:05:18,988 - ERROR [Contest,0 8498 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/6363422880026823341 (172.18.0.3)`
- x34 ContestWebServer-0 ERROR: `2026-10-07 18:05:19,052 - ERROR [Contest,0 8498 web::log_request] 500 GET /loada/tasks/umbral/submissions/6363422880026823341 (172.18.0.3) 60091.05ms`
- x34 cms ERROR: `2026-10-07 18:05:18,988 - ERROR [Contest,0 8498 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/6363422880026823341 (172.18.0.3)`
- x34 cms ERROR: `2026-10-07 18:05:19,052 - ERROR [Contest,0 8498 web::log_request] 500 GET /loada/tasks/umbral/submissions/6363422880026823341 (172.18.0.3) 60091.05ms`
- x29 ContestWebServer-0 ERROR: `2026-10-07 18:05:13,668 - ERROR [Contest,0 8298 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/2552715599676258569 (172.18.0.3)`
- x29 ContestWebServer-0 ERROR: `2026-10-07 18:05:13,692 - ERROR [Contest,0 8298 web::log_request] 500 GET /loada/tasks/cadena/submissions/2552715599676258569 (172.18.0.3) 60026.26ms`
- x29 cms ERROR: `2026-10-07 18:05:13,668 - ERROR [Contest,0 8298 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/2552715599676258569 (172.18.0.3)`
- x29 cms ERROR: `2026-10-07 18:05:13,692 - ERROR [Contest,0 8298 web::log_request] 500 GET /loada/tasks/cadena/submissions/2552715599676258569 (172.18.0.3) 60026.26ms`
- x23 ContestWebServer-0 ERROR: `2026-10-07 18:05:16,526 - ERROR [Contest,0 8491 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/1035660612706472444 (172.18.0.3)`
- x23 ContestWebServer-0 ERROR: `2026-10-07 18:05:16,547 - ERROR [Contest,0 8491 web::log_request] 500 GET /loada/tasks/libre/submissions/1035660612706472444 (172.18.0.3) 60053.49ms`
- x23 cms ERROR: `2026-10-07 18:05:16,526 - ERROR [Contest,0 8491 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/1035660612706472444 (172.18.0.3)`
- x23 cms ERROR: `2026-10-07 18:05:16,547 - ERROR [Contest,0 8491 web::log_request] 500 GET /loada/tasks/libre/submissions/1035660612706472444 (172.18.0.3) 60053.49ms`
- x21 ContestWebServer-0 ERROR: `2026-10-07 18:05:22,432 - ERROR [Contest,0 8515 web::log_exception] Uncaught exception GET /loadb/tasks/sumab/submissions/308596278248848271 (172.18.0.3)`
- x21 ContestWebServer-0 ERROR: `2026-10-07 18:05:22,439 - ERROR [Contest,0 8515 web::log_request] 500 GET /loadb/tasks/sumab/submissions/308596278248848271 (172.18.0.3) 60048.32ms`
- x21 ContestWebServer-0 ERROR: `2026-10-07 18:05:29,575 - ERROR [Contest,0 8287 web::log_exception] Uncaught exception GET /loadb/tasks/umbralb/submissions/7594317626606653747 (172.18.0.3)`
- x21 ContestWebServer-0 ERROR: `2026-10-07 18:05:29,589 - ERROR [Contest,0 8287 web::log_request] 500 GET /loadb/tasks/umbralb/submissions/7594317626606653747 (172.18.0.3) 60037.06ms`
- x21 cms ERROR: `2026-10-07 18:05:22,432 - ERROR [Contest,0 8515 web::log_exception] Uncaught exception GET /loadb/tasks/sumab/submissions/308596278248848271 (172.18.0.3)`
- x21 cms ERROR: `2026-10-07 18:05:22,439 - ERROR [Contest,0 8515 web::log_request] 500 GET /loadb/tasks/sumab/submissions/308596278248848271 (172.18.0.3) 60048.32ms`
- x21 cms ERROR: `2026-10-07 18:05:29,575 - ERROR [Contest,0 8287 web::log_exception] Uncaught exception GET /loadb/tasks/umbralb/submissions/7594317626606653747 (172.18.0.3)`
- x21 cms ERROR: `2026-10-07 18:05:29,589 - ERROR [Contest,0 8287 web::log_request] 500 GET /loadb/tasks/umbralb/submissions/7594317626606653747 (172.18.0.3) 60037.06ms`
- x18 ContestWebServer-0 ERROR: `2026-10-07 18:05:11,758 - ERROR [Contest,0 8162 web::log_exception] Uncaught exception GET /loadb/tasks/mulb/submissions/6142636421168339739 (172.18.0.3)`
- x18 ContestWebServer-0 ERROR: `2026-10-07 18:05:11,768 - ERROR [Contest,0 8162 web::log_request] 500 GET /loadb/tasks/mulb/submissions/6142636421168339739 (172.18.0.3) 60026.08ms`
- x18 cms ERROR: `2026-10-07 18:05:11,758 - ERROR [Contest,0 8162 web::log_exception] Uncaught exception GET /loadb/tasks/mulb/submissions/6142636421168339739 (172.18.0.3)`
- x18 cms ERROR: `2026-10-07 18:05:11,768 - ERROR [Contest,0 8162 web::log_request] 500 GET /loadb/tasks/mulb/submissions/6142636421168339739 (172.18.0.3) 60026.08ms`
- x7 ContestWebServer-0 ERROR: `2026-10-07 18:05:19,575 - ERROR [Contest,0 8362 workflow::accept_submission] Storage failed! QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60 (Background on this error at: http://sqlalche.me/e/13/3o7r)`
- x7 ContestWebServer-0 ERROR: `2026-10-07 18:05:32,514 - ERROR [Contest,0 8569 web::log_exception] Uncaught exception POST /loada/tasks/libre/submit (172.18.0.3)`

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 93% max 239% (100% = 1 core); last mem 1008MiB
- cmsload-upstream-db-1: CPU mean 12% max 48% (100% = 1 core); last mem 121.8MiB
- cmsload-upstream-driver-1: CPU mean 3% max 10% (100% = 1 core); last mem 1.266MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 27.2MiB
