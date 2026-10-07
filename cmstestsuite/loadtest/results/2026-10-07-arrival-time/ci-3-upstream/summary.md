# Run ci-3-upstream

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791397558.772174.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 258 | 0 | 0.012 | 0.029 | 0.071 | 0.132 |
| contest_page | steady | 107 | 0 | 0.119 | 1.308 | 2.523 | 5.213 |
| home_anon | login_burst | 250 | 0 | 0.007 | 0.362 | 0.566 | 0.921 |
| home_logged | login_burst | 250 | 0 | 0.009 | 0.296 | 1.435 | 2.000 |
| login | login_burst | 250 | 0 | 0.282 | 0.844 | 1.669 | 2.353 |
| notifications | login_burst | 499 | 0 | 0.011 | 0.485 | 1.156 | 1.599 |
| notifications | start_burst | 500 | 0 | 0.015 | 0.120 | 0.248 | 0.432 |
| notifications | steady | 9363 | 0 | 0.182 | 2.215 | 3.392 | 6.292 |
| notifications | end_burst | 2310 | 36 | 1.555 | 11.248 | 60.147 | 61.149 |
| notifications | drain | 6442 | 3 | 0.123 | 5.786 | 54.942 | 61.757 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.001 | 0.002 | 0.007 |
| rws_scores | start_burst | 30 | 0 | 0.001 | 0.003 | 0.011 | 0.015 |
| rws_scores | steady | 568 | 0 | 0.002 | 0.015 | 0.042 | 0.073 |
| rws_scores | end_burst | 149 | 0 | 0.004 | 0.014 | 0.021 | 0.034 |
| rws_scores | drain | 429 | 0 | 0.003 | 0.013 | 0.024 | 0.095 |
| statement | start_burst | 421 | 0 | 0.022 | 0.061 | 0.185 | 0.392 |
| status_poll | start_burst | 87 | 0 | 0.032 | 0.178 | 0.279 | 0.301 |
| status_poll | steady | 6134 | 0 | 0.126 | 1.604 | 3.185 | 6.730 |
| status_poll | end_burst | 3400 | 87 | 2.123 | 14.106 | 60.279 | 61.199 |
| status_poll | drain | 2172 | 4 | 0.471 | 49.950 | 58.390 | 62.496 |
| submission_details | start_burst | 3 | 0 | 0.129 | 0.223 | 0.232 | 0.234 |
| submission_details | steady | 273 | 0 | 0.227 | 1.373 | 2.616 | 3.671 |
| submission_details | end_burst | 84 | 1 | 2.218 | 12.810 | 60.243 | 60.463 |
| submission_details | drain | 185 | 0 | 0.302 | 9.659 | 20.234 | 59.583 |
| submissions_list | start_burst | 3 | 0 | 0.048 | 0.053 | 0.054 | 0.054 |
| submissions_list | steady | 108 | 0 | 0.257 | 1.825 | 2.429 | 3.226 |
| submissions_page | start_burst | 22 | 0 | 0.059 | 0.144 | 0.203 | 0.219 |
| submissions_page | steady | 645 | 0 | 0.229 | 1.554 | 3.235 | 4.498 |
| submissions_page | end_burst | 411 | 8 | 2.731 | 11.961 | 60.372 | 61.332 |
| submissions_page | drain | 30 | 0 | 17.614 | 26.872 | 31.960 | 33.816 |
| submit | start_burst | 22 | 0 | 0.032 | 0.130 | 0.297 | 0.342 |
| submit | steady | 648 | 0 | 0.183 | 2.565 | 4.653 | 5.835 |
| submit | end_burst | 469 | 17 | 4.503 | 65.087 | 82.621 | 89.927 |
| submit | drain | 12 | 0 | 20.015 | 47.581 | 57.958 | 60.552 |
| task_description | start_burst | 625 | 0 | 0.012 | 0.036 | 0.125 | 0.189 |
| task_description | steady | 205 | 0 | 0.107 | 1.850 | 3.343 | 4.163 |
| task_description | end_burst | 154 | 9 | 2.033 | 60.078 | 60.268 | 61.121 |

All phases: login p50 0.282 s, p95 0.844 s; submit p50 0.646 s, p95 60.083 s; submit in end_burst p95 65.087 s.

Failed requests by (kind, status, error):

- status_poll status=500 : 91
- notifications status=500 : 39
- submit status=500 : 17
- task_description status=500 : 9
- submissions_page status=500 : 8
- submission_details status=500 : 1

Peak CWS request rate: 88 req/s (1 s buckets); mean 14.6 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1151; rejected by CWS: 43; DB rows: 1108.
  - rejected b046 sumab status=500 location=None
  - rejected a089 libre status=302 location=../../../loada/tasks/libre/submissions
  - rejected a139 cadena status=302 location=../../../loada/tasks/cadena/submissions
  - rejected a152 umbral status=500 location=None
  - rejected a080 cadena status=500 location=None
  - rejected a038 umbral status=500 location=None
  - rejected b013 sumab status=302 location=../../../loadb/tasks/sumab/submissions
  - rejected b065 mulb status=302 location=../../../loadb/tasks/mulb/submissions
  - rejected a165 libre status=500 location=None
  - rejected a164 umbral status=500 location=None

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 22 | 11.1 | 14.7 | 22.2 | 13.4 | 17.2 | 29.9 |
| steady | 648 | 126.1 | 164.4 | 180.9 | 152.1 | 217.8 | 269.2 |
| end_burst | 430 | 360.6 | 591.8 | 627.8 | 416.7 | 742.0 | 845.4 |

All: server submit->scored p50 145.9 s, p95 550.5 s, max 627.8 s.
Last submission scored 624 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 8
Score mismatches vs expected: 0
Mix: {'ce': 77, 'wa_small': 116, 'wa_zero': 61, 're': 88, 'ac': 335, 'tle': 143, 'wa_overflow': 165, 'ac_py': 123, 'ac_java': 43}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1108}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=539 p50 1.2 p95 2.0 max 2.3 s

## Internals (monitor, 2 s samples)

ES queue: max 7275 operations (max 303 distinct entries); workers busy mean 6.3, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:63/1.7 +1:98/6.3 +2:212/6.5 +3:144/8.0 +4:361/7.8 +5:591/8.0 +6:761/7.9 +7:1036/8.0 +8:989/8.0 +9:901/8.0 +10:959/8.0 +11:1033/7.9 +12:1274/8.0 +13:1383/8.0 +14:1372/8.0 +15:1336/8.0 +16:1345/7.9 +17:1263/8.0 +18:1145/7.9 +19:1140/7.9 +20:1233/8.0 +21:1805/8.0 +22:2628/8.0 +23:4423/8.0 +24:6240/7.8 +25:7275/7.9 +26:6958/7.9 +27:6683/7.9 +28:5958/8.0 +29:5133/8.0 +30:4483/7.9 +31:3708/7.8 +32:2808/8.0 +33:1858/8.0 +34:958/7.9 +35:92/2.7 +36:0/0.0 +37:0/0.0 +38:0/0.0 +39:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 3.2 | 98.7 | 376.0 | 2 | 0 | 0 |
| CWS0 | 10.4 | 512.8 | 1132.6 | 87 | 1 | 0 |
| CWS1 | 9.3 | 559.1 | 1603.1 | 75 | 3 | 0 |
| ES | 5.1 | 374.5 | 2408.6 | 24 | 3 | 0 |
| PS | 3.3 | 60.2 | 394.2 | 2 | 0 | 0 |
| SS | 3.7 | 163.1 | 352.0 | 3 | 0 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 5.9 | 72 |
| ContestWebServer0 | 15 | 296.0 | 135 |
| ContestWebServer1 | 15 | 294.9 | 139 |
| EvaluationService0 | 15 | 159.5 | 77 |
| LogService0 | 0 | 72.1 | 66 |
| ProxyService0 | 3 | 16.2 | 78 |
| ScoringService0 | 1 | 36.5 | 74 |
| Worker0 | 1 | 95.3 | 81 |
| Worker1 | 1 | 95.1 | 73 |
| Worker2 | 1 | 93.1 | 72 |
| Worker3 | 1 | 96.8 | 72 |
| Worker4 | 1 | 96.3 | 73 |
| Worker5 | 1 | 92.2 | 74 |
| Worker6 | 1 | 96.9 | 77 |
| Worker7 | 1 | 99.1 | 74 |

PostgreSQL backends for cmsdb: max 59 (max_connections 100); max 'active' 9; max lock waits 0; longest idle-in-transaction 60.5 s.

## Service logs

Log files: 16

| service | WARNING | ERROR | CRITICAL |
|---|---|---|---|
| AdminWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-0 | 0 | 110 | 0 |
| ContestWebServer-1 | 0 | 399 | 0 |
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
| cms | 0 | 509 | 0 |

Pattern hits:
- ContestWebServer-0: QueuePool x110
- ContestWebServer-0: TimeoutError x180
- ContestWebServer-0: Traceback x72
- ContestWebServer-0: timeout/timed out x182
- ContestWebServer-1: QueuePool x399
- ContestWebServer-1: TimeoutError x645
- ContestWebServer-1: Traceback x258
- ContestWebServer-1: timeout/timed out x657
- EvaluationService-0: missed operation(s) x23
- ProxyService-0: missed operation(s) x8
- ScoringService-0: missed operation(s) x8
- cms: QueuePool x509
- cms: TimeoutError x825
- cms: Traceback x330
- cms: missed operation(s) x39
- cms: timeout/timed out x839
- EvaluationService-0: sweeper found 2 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 2 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):
- x165 cms ERROR: `2026-10-07 18:51:34,851 - ERROR [Contest,1 8537 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x129 ContestWebServer-1 ERROR: `2026-10-07 18:51:34,851 - ERROR [Contest,1 8537 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x36 ContestWebServer-0 ERROR: `2026-10-07 18:51:49,422 - ERROR [Contest,0 8636 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x24 cms ERROR: `2026-10-07 18:51:42,030 - ERROR [Contest,1 8692 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x24 cms ERROR: `2026-10-07 18:51:42,032 - ERROR [Contest,1 8692 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60006.20ms`
- x22 cms ERROR: `2026-10-07 18:51:36,900 - ERROR [Contest,1 8434 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/5769373814308910482 (172.18.0.4)`
- x22 cms ERROR: `2026-10-07 18:51:36,926 - ERROR [Contest,1 8434 web::log_request] 500 GET /loada/tasks/suma/submissions/5769373814308910482 (172.18.0.4) 60070.74ms`
- x20 ContestWebServer-1 ERROR: `2026-10-07 18:51:36,900 - ERROR [Contest,1 8434 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/5769373814308910482 (172.18.0.4)`
- x20 ContestWebServer-1 ERROR: `2026-10-07 18:51:36,926 - ERROR [Contest,1 8434 web::log_request] 500 GET /loada/tasks/suma/submissions/5769373814308910482 (172.18.0.4) 60070.74ms`
- x20 cms ERROR: `2026-10-07 18:51:39,490 - ERROR [Contest,1 8681 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/442276355414549658 (172.18.0.4)`
- x20 cms ERROR: `2026-10-07 18:51:39,506 - ERROR [Contest,1 8681 web::log_request] 500 GET /loada/tasks/cadena/submissions/442276355414549658 (172.18.0.4) 60038.37ms`
- x18 ContestWebServer-1 ERROR: `2026-10-07 18:51:42,030 - ERROR [Contest,1 8692 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x18 ContestWebServer-1 ERROR: `2026-10-07 18:51:42,032 - ERROR [Contest,1 8692 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60006.20ms`
- x18 cms ERROR: `2026-10-07 18:51:40,412 - ERROR [Contest,1 8314 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/8167573416712360234 (172.18.0.4)`
- x18 cms ERROR: `2026-10-07 18:51:40,447 - ERROR [Contest,1 8314 web::log_request] 500 GET /loada/tasks/libre/submissions/8167573416712360234 (172.18.0.4) 60043.12ms`
- x15 ContestWebServer-1 ERROR: `2026-10-07 18:51:40,412 - ERROR [Contest,1 8314 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/8167573416712360234 (172.18.0.4)`
- x15 ContestWebServer-1 ERROR: `2026-10-07 18:51:40,447 - ERROR [Contest,1 8314 web::log_request] 500 GET /loada/tasks/libre/submissions/8167573416712360234 (172.18.0.4) 60043.12ms`
- x15 cms ERROR: `2026-10-07 18:51:35,299 - ERROR [Contest,1 8349 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.4)`
- x15 cms ERROR: `2026-10-07 18:51:35,303 - ERROR [Contest,1 8349 web::log_request] 500 GET /loadb/notifications (172.18.0.4) 60006.69ms`
- x14 ContestWebServer-1 ERROR: `2026-10-07 18:51:39,490 - ERROR [Contest,1 8681 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/442276355414549658 (172.18.0.4)`
- x14 ContestWebServer-1 ERROR: `2026-10-07 18:51:39,506 - ERROR [Contest,1 8681 web::log_request] 500 GET /loada/tasks/cadena/submissions/442276355414549658 (172.18.0.4) 60038.37ms`
- x14 cms ERROR: `2026-10-07 18:51:40,590 - ERROR [Contest,1 8568 workflow::accept_submission] Storage failed! QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60 (Background on this error at: http://sqlalche.me/e/13/3o7r)`
- x14 cms ERROR: `2026-10-07 18:51:41,507 - ERROR [Contest,1 8479 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/8043866877112921329 (172.18.0.4)`
- x14 cms ERROR: `2026-10-07 18:51:41,527 - ERROR [Contest,1 8479 web::log_request] 500 GET /loada/tasks/umbral/submissions/8043866877112921329 (172.18.0.4) 60174.32ms`
- x12 ContestWebServer-1 ERROR: `2026-10-07 18:51:35,299 - ERROR [Contest,1 8349 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.4)`
- x12 ContestWebServer-1 ERROR: `2026-10-07 18:51:35,303 - ERROR [Contest,1 8349 web::log_request] 500 GET /loadb/notifications (172.18.0.4) 60006.69ms`
- x12 ContestWebServer-1 ERROR: `2026-10-07 18:51:40,590 - ERROR [Contest,1 8568 workflow::accept_submission] Storage failed! QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60 (Background on this error at: http://sqlalche.me/e/13/3o7r)`
- x9 ContestWebServer-1 ERROR: `2026-10-07 18:51:40,263 - ERROR [Contest,1 8192 web::log_exception] Uncaught exception GET /loadb/tasks/sumab/submissions/1292277120118585815 (172.18.0.4)`
- x9 ContestWebServer-1 ERROR: `2026-10-07 18:51:40,275 - ERROR [Contest,1 8192 web::log_request] 500 GET /loadb/tasks/sumab/submissions/1292277120118585815 (172.18.0.4) 60013.68ms`
- x9 ContestWebServer-1 ERROR: `2026-10-07 18:51:41,507 - ERROR [Contest,1 8479 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/8043866877112921329 (172.18.0.4)`
- x9 ContestWebServer-1 ERROR: `2026-10-07 18:51:41,527 - ERROR [Contest,1 8479 web::log_request] 500 GET /loada/tasks/umbral/submissions/8043866877112921329 (172.18.0.4) 60174.32ms`
- x9 cms ERROR: `2026-10-07 18:51:40,263 - ERROR [Contest,1 8192 web::log_exception] Uncaught exception GET /loadb/tasks/sumab/submissions/1292277120118585815 (172.18.0.4)`
- x9 cms ERROR: `2026-10-07 18:51:40,275 - ERROR [Contest,1 8192 web::log_request] 500 GET /loadb/tasks/sumab/submissions/1292277120118585815 (172.18.0.4) 60013.68ms`
- x6 ContestWebServer-0 ERROR: `2026-10-07 18:51:50,317 - ERROR [Contest,0 8639 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x6 ContestWebServer-0 ERROR: `2026-10-07 18:51:50,383 - ERROR [Contest,0 8639 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60072.31ms`
- x6 ContestWebServer-0 ERROR: `2026-10-07 18:51:50,823 - ERROR [Contest,0 8319 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/3809255664586642289 (172.18.0.4)`
- x6 ContestWebServer-0 ERROR: `2026-10-07 18:51:50,841 - ERROR [Contest,0 8319 web::log_request] 500 GET /loada/tasks/cadena/submissions/3809255664586642289 (172.18.0.4) 60070.91ms`
- x6 ContestWebServer-1 ERROR: `2026-10-07 18:51:44,440 - ERROR [Contest,1 8578 web::log_exception] Uncaught exception POST /loada/tasks/umbral/submit (172.18.0.4)`
- x6 ContestWebServer-1 ERROR: `2026-10-07 18:51:44,455 - ERROR [Contest,1 8578 web::log_request] 500 POST /loada/tasks/umbral/submit (172.18.0.4) 60056.35ms`
- x6 cms ERROR: `2026-10-07 18:51:44,440 - ERROR [Contest,1 8578 web::log_exception] Uncaught exception POST /loada/tasks/umbral/submit (172.18.0.4)`

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 103% max 233% (100% = 1 core); last mem 999.8MiB
- cmsload-upstream-db-1: CPU mean 12% max 46% (100% = 1 core); last mem 116MiB
- cmsload-upstream-driver-1: CPU mean 2% max 8% (100% = 1 core); last mem 1.371MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 40.81MiB
