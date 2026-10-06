#!/usr/bin/env python3
"""Measure how long finished worker results wait before ES accepts them.

For every worker job group, lag = ES "`<op>' succeeded" log time (logged
after _action_finished_sync gets post_finish_lock) - worker "Finished job
group" time. Usage: lockwait.py out/<run>
"""
import datetime, glob, json, os, re, sys
run = sys.argv[1]
ts = lambda l: datetime.datetime.strptime(l[:23], '%Y-%m-%d %H:%M:%S,%f').replace(tzinfo=datetime.timezone.utc).timestamp()
job_re = re.compile(r'\[(evaluate|compile) submission (\d+)(?: on testcase (\S+?))?\] Finished job')
finished = {}  # key -> worker group finish time
for f in glob.glob(os.path.join(run, 'cmslog', 'Worker-*', '2026*.log')):
    last = None
    for l in open(f, errors='replace'):
        m = job_re.search(l)
        if m:
            last = (m.group(1), int(m.group(2)), m.group(3))
        elif 'Finished job group' in l and last:
            finished[last] = ts(l)
            last = None
es_re = re.compile(r"`(evaluate|compile) on (\d+) against dataset \d+(?:, testcase (\S+?))?, archiving sandbox \w+' succeeded")
lags = []
for f in glob.glob(os.path.join(run, 'cmslog', 'EvaluationService-0', '2026*.log')):
    for l in open(f, errors='replace'):
        m = es_re.search(l)
        if m:
            key = (m.group(1), int(m.group(2)), m.group(3))
            if key in finished:
                lags.append((finished[key], ts(l) - finished.pop(key)))
users = json.load(open(os.path.join(run, 'users.json')))
def q(v, p):
    v = sorted(v); return v[int((len(v) - 1) * p)] if v else float('nan')
for name, a, b in [('all', 0, 1e12), ('steady', users['start'] + 60, users['stop'] - 300),
                   ('end_burst+drain', users['stop'] - 300, 1e12)]:
    v = [x for t, x in lags if a <= t < b]
    print('%s: job groups %d, result wait p50 %.2f p95 %.2f p99 %.2f max %.2f s, total %.0f worker-s'
          % (name, len(v), q(v, .5), q(v, .95), q(v, .99), max(v) if v else 0, sum(v)))
