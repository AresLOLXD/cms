Pair jobs: success; runs with a metrics.json: 8.

## Median per target, profile portable

| metric | fork | upstream |
|---|---|---|
| runs | 4 | 4 |
| users | 250 | 250 |
| submissions_sent | 1094 | 1102 |
| submissions_rejected | 17 | 47.5 |
| login_failures | 0 | 0 |
| http_errors | 0 | 183 |
| score_mismatches | 0 | 0 |
| rws_pairs | 700 | 700 |
| rws_mismatches | 0 | 0 |
| login_p50 | 0.28 | 0.284 |
| login_p95 | 0.296 | 1.08 |
| submit_p50 | 0.23 | 0.643 |
| submit_p95 | 11.8 | 59.2 |
| submit_end_p95 | 13.6 | 62.8 |
| scored_p50 | 168 | 154 |
| scored_p95 | 687 | 632 |
| scored_max | 759 | 716 |
| drain_after_stop_s | 756 | 712 |
| peak_pg_connections | 27 | 56.5 |

## Every run

| metric | ci-1-fork | ci-2-fork | ci-3-fork | ci-4-fork | ci-1-upstream | ci-2-upstream | ci-3-upstream | ci-4-upstream |
|---|---|---|---|---|---|---|---|---|
| users | 250 | 250 | 250 | 250 | 250 | 250 | 250 | 250 |
| submissions_sent | 1094 | 1042 | 1094 | 1129 | 1108 | 1053 | 1151 | 1095 |
| submissions_rejected | 17 | 22 | 17 | 15 | 1 | 52 | 43 | 52 |
| login_failures | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| http_errors | 0 | 0 | 0 | 0 | 0 | 201 | 165 | 385 |
| score_mismatches | 0 | 0 | 2 | 0 | 1 | 0 | 0 | 0 |
| rws_pairs | 700 | 700 | 700 | 700 | 700 | 700 | 700 | 700 |
| rws_mismatches | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| login_p50 | 0.279 | 0.28 | 0.278 | 0.313 | 0.284 | 0.285 | 0.282 | 0.318 |
| login_p95 | 0.295 | 0.297 | 0.292 | 0.334 | 1.05 | 1.12 | 0.844 | 1.52 |
| submit_p50 | 0.221 | 0.239 | 0.282 | 0.22 | 0.493 | 1.26 | 0.646 | 0.639 |
| submit_p95 | 9.73 | 14.2 | 14 | 9.29 | 7.08 | 60.5 | 60.1 | 58.4 |
| submit_end_p95 | 11 | 15.6 | 16.6 | 11.5 | 11 | 72.4 | 65.1 | 60.6 |
| scored_p50 | 185 | 228 | 150 | 135 | 152 | 202 | 146 | 156 |
| scored_p95 | 704 | 776 | 628 | 670 | 900 | 668 | 550 | 596 |
| scored_max | 785 | 874 | 700 | 733 | 992 | 730 | 628 | 702 |
| drain_after_stop_s | 782 | 871 | 697 | 730 | 989 | 724 | 624 | 699 |
| peak_pg_connections | 27 | 26 | 27 | 27 | 54 | 57 | 59 | 56 |
