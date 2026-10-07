# Run ci-2-upstream

Target upstream, profile portable.
Users: 250 (loada 175, loadb 75). Contest 1500 s; start 1791394778.577885.

## HTTP latency by request kind and phase (seconds)

| kind | phase | n | errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| contest_page | start_burst | 256 | 0 | 0.020 | 0.441 | 0.601 | 0.949 |
| contest_page | steady | 92 | 0 | 0.354 | 3.705 | 5.389 | 6.586 |
| home_anon | login_burst | 250 | 0 | 0.009 | 0.345 | 0.758 | 0.804 |
| home_logged | login_burst | 250 | 0 | 0.011 | 0.606 | 1.152 | 1.479 |
| login | login_burst | 250 | 0 | 0.285 | 1.124 | 1.399 | 1.591 |
| notifications | login_burst | 499 | 0 | 0.013 | 0.700 | 1.375 | 1.795 |
| notifications | start_burst | 493 | 0 | 0.087 | 1.585 | 2.367 | 2.540 |
| notifications | steady | 9239 | 0 | 0.389 | 3.243 | 6.653 | 18.742 |
| notifications | end_burst | 2243 | 42 | 1.832 | 50.774 | 60.083 | 60.551 |
| notifications | drain | 8025 | 39 | 0.089 | 1.955 | 43.393 | 60.724 |
| rws_scores | login_burst | 96 | 0 | 0.001 | 0.002 | 0.002 | 0.008 |
| rws_scores | start_burst | 30 | 0 | 0.002 | 0.006 | 0.007 | 0.007 |
| rws_scores | steady | 568 | 0 | 0.003 | 0.013 | 0.035 | 0.109 |
| rws_scores | end_burst | 149 | 0 | 0.004 | 0.016 | 0.036 | 0.037 |
| rws_scores | drain | 523 | 0 | 0.003 | 0.015 | 0.027 | 0.040 |
| statement | start_burst | 427 | 0 | 0.034 | 0.452 | 0.778 | 1.995 |
| status_poll | start_burst | 106 | 0 | 0.340 | 1.806 | 2.165 | 2.188 |
| status_poll | steady | 6092 | 0 | 0.230 | 2.971 | 5.856 | 15.588 |
| status_poll | end_burst | 2748 | 51 | 2.379 | 49.646 | 60.104 | 61.109 |
| status_poll | drain | 2547 | 33 | 0.207 | 33.434 | 60.019 | 60.543 |
| submission_details | start_burst | 4 | 0 | 1.412 | 3.661 | 3.939 | 4.009 |
| submission_details | steady | 241 | 0 | 0.391 | 3.849 | 6.111 | 7.049 |
| submission_details | end_burst | 72 | 1 | 1.464 | 9.423 | 57.435 | 60.049 |
| submission_details | drain | 179 | 1 | 0.205 | 18.133 | 43.204 | 60.096 |
| submissions_list | start_burst | 5 | 0 | 0.772 | 2.475 | 2.694 | 2.749 |
| submissions_list | steady | 95 | 0 | 0.501 | 3.719 | 6.734 | 7.146 |
| submissions_page | start_burst | 25 | 0 | 0.360 | 2.268 | 2.584 | 2.642 |
| submissions_page | steady | 589 | 0 | 0.352 | 3.169 | 5.118 | 8.448 |
| submissions_page | end_burst | 328 | 4 | 2.492 | 16.839 | 57.628 | 60.208 |
| submissions_page | drain | 59 | 0 | 18.906 | 46.527 | 56.344 | 56.738 |
| submit | start_burst | 26 | 0 | 0.362 | 3.322 | 3.351 | 3.358 |
| submit | steady | 588 | 0 | 0.399 | 5.175 | 9.155 | 12.816 |
| submit | end_burst | 420 | 25 | 5.687 | 72.427 | 99.725 | 117.697 |
| submit | drain | 19 | 1 | 38.053 | 54.915 | 59.032 | 60.061 |
| task_description | start_burst | 627 | 0 | 0.018 | 0.391 | 0.802 | 2.267 |
| task_description | steady | 166 | 0 | 0.219 | 2.669 | 5.632 | 7.364 |
| task_description | end_burst | 127 | 4 | 2.162 | 56.550 | 60.033 | 60.756 |

All phases: login p50 0.285 s, p95 1.124 s; submit p50 1.259 s, p95 60.482 s; submit in end_burst p95 72.427 s.

Failed requests by (kind, status, error):

- status_poll status=500 : 84
- notifications status=500 : 81
- submit status=500 : 26
- submissions_page status=500 : 4
- task_description status=500 : 4
- submission_details status=500 : 2

Peak CWS request rate: 91 req/s (1 s buckets); mean 14.0 req/s.

## Submissions

Login failures (gave up after 5 tries): 0
Submissions sent: 1053; rejected by CWS: 52; DB rows: 1001.
  - rejected a015 libre status=302 location=../../../loada/tasks/libre/submissions
  - rejected a040 suma status=500 location=None
  - rejected b006 mulb status=500 location=None
  - rejected a023 umbral status=500 location=None
  - rejected a157 suma status=500 location=None
  - rejected a024 suma status=302 location=../../../loada/tasks/suma/submissions
  - rejected a145 cadena status=302 location=../../../loada/tasks/cadena/submissions
  - rejected a153 libre status=500 location=None
  - rejected b051 umbralb status=302 location=../../../loadb/tasks/umbralb/submissions
  - rejected a037 umbral status=500 location=None

| phase | sent | server submit->scored p50 | p95 | max | perceived (browser backoff) p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| start_burst | 26 | 20.9 | 30.3 | 31.8 | 27.6 | 42.7 | 45.4 |
| steady | 588 | 187.5 | 237.0 | 249.3 | 222.0 | 298.2 | 634.3 |
| end_burst | 383 | 459.2 | 696.5 | 729.5 | 540.4 | 868.3 | 1032.0 |

All: server submit->scored p50 202.4 s, p95 667.5 s, max 729.5 s.
Last submission scored 724 s after the contest stop.

Never scored (stuck): 0
Rows not identified on the submissions page: 4
Score mismatches vs expected: 0
Mix: {'ac': 303, 'ce': 72, 'tle': 135, 'wa_overflow': 131, 'wa_zero': 53, 're': 86, 'wa_small': 120, 'ac_py': 119, 'ac_java': 34}
(compilation_tries, evaluation_tries) histogram: {(0, 0): 1001}

## Ranking (RWS)

Final RWS vs CMS task scores in the ranked contests (loada): 700 (user, task) pairs, 0 differ.
Ranking lag (RWS change seen - latest scored_at; 2 s polling): n=481 p50 1.1 p95 2.0 max 2.3 s

## Internals (monitor, 2 s samples)

ES queue: max 6893 operations (max 291 distinct entries); workers busy mean 6.3, max 8 of 8.

Queue per minute (max ops / mean busy workers):
-4:0/0.0 -3:0/0.0 -2:0/0.0 -1:0/0.0 +0:135/4.1 +1:174/7.7 +2:393/7.9 +3:510/8.0 +4:696/8.0 +5:903/7.9 +6:1199/8.0 +7:1210/8.0 +8:1243/8.0 +9:1451/8.0 +10:1499/8.0 +11:1551/8.0 +12:1623/8.0 +13:1631/8.0 +14:1579/8.0 +15:1573/7.9 +16:1585/8.0 +17:1337/8.0 +18:1494/8.0 +19:1664/8.0 +20:1990/7.9 +21:2356/7.6 +22:3042/7.9 +23:4041/7.8 +24:6288/7.7 +25:6320/8.0 +26:6747/8.0 +27:6893/8.0 +28:6390/7.9 +29:5590/8.0 +30:4740/8.0 +31:4240/8.0 +32:3515/8.0 +33:2890/7.9 +34:2090/8.0 +35:1315/7.9 +36:640/7.6 +37:0/0.0 +38:0/0.0 +39:0/0.0 +40:0/0.0 +41:0/0.0 +42:0/0.0

| service | echo p50 ms | p99 ms | max ms | samples >250 ms | >1 s | RPC errors |
|---|---|---|---|---|---|---|
| AWS | 3.5 | 64.0 | 2441.7 | 2 | 1 | 0 |
| CWS0 | 13.3 | 775.6 | 2754.0 | 134 | 6 | 0 |
| CWS1 | 10.8 | 819.1 | 1897.0 | 162 | 8 | 0 |
| ES | 6.1 | 411.8 | 2487.1 | 26 | 5 | 0 |
| PS | 3.8 | 114.2 | 752.4 | 4 | 0 | 0 |
| SS | 3.6 | 136.7 | 1307.2 | 5 | 1 | 0 |

| process | max PG conns | CPU s during run | max RSS MB |
|---|---|---|---|
| AdminWebServer0 | 1 | 7.5 | 73 |
| ContestWebServer0 | 17 | 345.8 | 141 |
| ContestWebServer1 | 16 | 355.1 | 136 |
| EvaluationService0 | 12 | 176.8 | 77 |
| LogService0 | 0 | 83.3 | 66 |
| ProxyService0 | 4 | 17.8 | 78 |
| ScoringService0 | 1 | 37.7 | 74 |
| Worker0 | 2 | 93.3 | 74 |
| Worker1 | 1 | 92.7 | 75 |
| Worker2 | 1 | 95.9 | 72 |
| Worker3 | 1 | 93.0 | 75 |
| Worker4 | 2 | 94.6 | 82 |
| Worker5 | 1 | 92.3 | 79 |
| Worker6 | 1 | 91.9 | 72 |
| Worker7 | 1 | 92.6 | 72 |

PostgreSQL backends for cmsdb: max 57 (max_connections 100); max 'active' 9; max lock waits 0; longest idle-in-transaction 60.7 s.

## Service logs

Log files: 16

| service | WARNING | ERROR | CRITICAL |
|---|---|---|---|
| AdminWebServer-0 | 0 | 0 | 0 |
| ContestWebServer-0 | 0 | 508 | 0 |
| ContestWebServer-1 | 0 | 103 | 0 |
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
| cms | 0 | 611 | 0 |

Pattern hits:
- ContestWebServer-0: QueuePool x508
- ContestWebServer-0: TimeoutError x840
- ContestWebServer-0: Traceback x336
- ContestWebServer-0: timeout/timed out x844
- ContestWebServer-1: QueuePool x103
- ContestWebServer-1: TimeoutError x165
- ContestWebServer-1: Traceback x66
- ContestWebServer-1: timeout/timed out x169
- EvaluationService-0: missed operation(s) x25
- ProxyService-0: missed operation(s) x8
- ScoringService-0: missed operation(s) x9
- cms: QueuePool x611
- cms: TimeoutError x1005
- cms: Traceback x402
- cms: missed operation(s) x42
- cms: timeout/timed out x1013
- EvaluationService-0: sweeper found 6 missed operations in total
- ProxyService-0: sweeper found 0 missed operations in total
- ScoringService-0: sweeper found 0 missed operations in total
- cms: sweeper found 6 missed operations in total

Distinct WARNING/ERROR messages (normalized digits):
- x201 cms ERROR: `2026-10-07 18:04:54,899 - ERROR [Contest,0 8254 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x168 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,899 - ERROR [Contest,0 8254 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x57 cms ERROR: `2026-10-07 18:04:54,904 - ERROR [Contest,0 8635 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x57 cms ERROR: `2026-10-07 18:04:54,914 - ERROR [Contest,0 8635 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60066.95ms`
- x46 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,904 - ERROR [Contest,0 8635 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x46 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,914 - ERROR [Contest,0 8635 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60066.95ms`
- x33 ContestWebServer-1 ERROR: `2026-10-07 18:05:02,303 - ERROR [Contest,1 8325 base::write_error] Uncaught exception (TimeoutError('QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60')) while processing a request: Traceback (most recent call last):`
- x24 cms ERROR: `2026-10-07 18:05:06,294 - ERROR [Contest,1 8532 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.4)`
- x24 cms ERROR: `2026-10-07 18:05:06,322 - ERROR [Contest,1 8532 web::log_request] 500 GET /loadb/notifications (172.18.0.4) 60046.22ms`
- x20 cms ERROR: `2026-10-07 18:04:55,730 - ERROR [Contest,0 8416 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/2679306943741118494 (172.18.0.4)`
- x20 cms ERROR: `2026-10-07 18:04:55,782 - ERROR [Contest,0 8416 web::log_request] 500 GET /loada/tasks/suma/submissions/2679306943741118494 (172.18.0.4) 60272.88ms`
- x19 ContestWebServer-0 ERROR: `2026-10-07 18:05:11,140 - ERROR [Contest,0 8518 web::log_exception] Uncaught exception GET /loadb/notifications (172.18.0.4)`
- x19 ContestWebServer-0 ERROR: `2026-10-07 18:05:11,142 - ERROR [Contest,0 8518 web::log_request] 500 GET /loadb/notifications (172.18.0.4) 60009.15ms`
- x18 ContestWebServer-0 ERROR: `2026-10-07 18:04:55,730 - ERROR [Contest,0 8416 web::log_exception] Uncaught exception GET /loada/tasks/suma/submissions/2679306943741118494 (172.18.0.4)`
- x18 ContestWebServer-0 ERROR: `2026-10-07 18:04:55,782 - ERROR [Contest,0 8416 web::log_request] 500 GET /loada/tasks/suma/submissions/2679306943741118494 (172.18.0.4) 60272.88ms`
- x16 cms ERROR: `2026-10-07 18:04:54,863 - ERROR [Contest,0 8254 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/137029376334789327 (172.18.0.4)`
- x16 cms ERROR: `2026-10-07 18:04:54,900 - ERROR [Contest,0 8254 web::log_request] 500 GET /loada/tasks/cadena/submissions/137029376334789327 (172.18.0.4) 60109.80ms`
- x15 cms ERROR: `2026-10-07 18:05:00,122 - ERROR [Contest,0 8650 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/6304801892910363603 (172.18.0.4)`
- x15 cms ERROR: `2026-10-07 18:05:00,134 - ERROR [Contest,0 8650 web::log_request] 500 GET /loada/tasks/umbral/submissions/6304801892910363603 (172.18.0.4) 60034.13ms`
- x14 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,863 - ERROR [Contest,0 8254 web::log_exception] Uncaught exception GET /loada/tasks/cadena/submissions/137029376334789327 (172.18.0.4)`
- x14 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,900 - ERROR [Contest,0 8254 web::log_request] 500 GET /loada/tasks/cadena/submissions/137029376334789327 (172.18.0.4) 60109.80ms`
- x13 ContestWebServer-0 ERROR: `2026-10-07 18:05:00,122 - ERROR [Contest,0 8650 web::log_exception] Uncaught exception GET /loada/tasks/umbral/submissions/6304801892910363603 (172.18.0.4)`
- x13 ContestWebServer-0 ERROR: `2026-10-07 18:05:00,134 - ERROR [Contest,0 8650 web::log_request] 500 GET /loada/tasks/umbral/submissions/6304801892910363603 (172.18.0.4) 60034.13ms`
- x13 cms ERROR: `2026-10-07 18:04:55,239 - ERROR [Contest,0 8298 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/984979718919568496 (172.18.0.4)`
- x13 cms ERROR: `2026-10-07 18:04:55,244 - ERROR [Contest,0 8298 web::log_request] 500 GET /loada/tasks/libre/submissions/984979718919568496 (172.18.0.4) 60058.72ms`
- x11 ContestWebServer-0 ERROR: `2026-10-07 18:04:55,239 - ERROR [Contest,0 8298 web::log_exception] Uncaught exception GET /loada/tasks/libre/submissions/984979718919568496 (172.18.0.4)`
- x11 ContestWebServer-0 ERROR: `2026-10-07 18:04:55,244 - ERROR [Contest,0 8298 web::log_request] 500 GET /loada/tasks/libre/submissions/984979718919568496 (172.18.0.4) 60058.72ms`
- x11 ContestWebServer-1 ERROR: `2026-10-07 18:05:02,281 - ERROR [Contest,1 8325 web::log_exception] Uncaught exception GET /loada/notifications (172.18.0.4)`
- x11 ContestWebServer-1 ERROR: `2026-10-07 18:05:02,314 - ERROR [Contest,1 8325 web::log_request] 500 GET /loada/notifications (172.18.0.4) 60043.96ms`
- x11 cms ERROR: `2026-10-07 18:04:57,861 - ERROR [Contest,0 8487 web::log_exception] Uncaught exception POST /loada/tasks/suma/submit (172.18.0.4)`
- x11 cms ERROR: `2026-10-07 18:04:57,873 - ERROR [Contest,0 8487 web::log_request] 500 POST /loada/tasks/suma/submit (172.18.0.4) 60015.83ms`
- x8 ContestWebServer-0 ERROR: `2026-10-07 18:04:57,861 - ERROR [Contest,0 8487 web::log_exception] Uncaught exception POST /loada/tasks/suma/submit (172.18.0.4)`
- x8 ContestWebServer-0 ERROR: `2026-10-07 18:04:57,873 - ERROR [Contest,0 8487 web::log_request] 500 POST /loada/tasks/suma/submit (172.18.0.4) 60015.83ms`
- x8 cms ERROR: `2026-10-07 18:04:54,731 - ERROR [Contest,0 8313 workflow::accept_submission] Storage failed! QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60 (Background on this error at: http://sqlalche.me/e/13/3o7r)`
- x8 cms ERROR: `2026-10-07 18:04:54,922 - ERROR [Contest,0 8368 web::log_exception] Uncaught exception GET /loadb/tasks/mulb/submissions/8802041972430389618 (172.18.0.4)`
- x8 cms ERROR: `2026-10-07 18:04:54,929 - ERROR [Contest,0 8368 web::log_request] 500 GET /loadb/tasks/mulb/submissions/8802041972430389618 (172.18.0.4) 60076.63ms`
- x8 cms ERROR: `2026-10-07 18:04:55,266 - ERROR [Contest,0 8443 web::log_exception] Uncaught exception GET /loadb/tasks/umbralb/submissions/2395874919701129947 (172.18.0.4)`
- x8 cms ERROR: `2026-10-07 18:04:55,289 - ERROR [Contest,0 8443 web::log_request] 500 GET /loadb/tasks/umbralb/submissions/2395874919701129947 (172.18.0.4) 60052.35ms`
- x7 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,922 - ERROR [Contest,0 8368 web::log_exception] Uncaught exception GET /loadb/tasks/mulb/submissions/8802041972430389618 (172.18.0.4)`
- x7 ContestWebServer-0 ERROR: `2026-10-07 18:04:54,929 - ERROR [Contest,0 8368 web::log_request] 500 GET /loadb/tasks/mulb/submissions/8802041972430389618 (172.18.0.4) 60076.63ms`

## PostgreSQL log

Slow statements (>1 s): 0
Errors/lock waits: 0

## Containers (docker stats, 5 s)

- cmsload-upstream-cms-1: CPU mean 100% max 205% (100% = 1 core); last mem 999.6MiB
- cmsload-upstream-db-1: CPU mean 14% max 69% (100% = 1 core); last mem 120.8MiB
- cmsload-upstream-driver-1: CPU mean 3% max 13% (100% = 1 core); last mem 1.25MiB
- cmsload-upstream-ranking-1: CPU mean 0% max 1% (100% = 1 core); last mem 27.09MiB
