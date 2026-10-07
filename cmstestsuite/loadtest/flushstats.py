#!/usr/bin/env python3
"""ES result-flush statistics and server latency per task/solution kind.

Flush = one _write_results_sync ("Starting commit process..." -> "Done"),
which holds post_finish_lock. Gap = time between two flush starts: every
two-phase / dependency stage of a submission waits for one flush.
Usage: flushstats.py out/<run>
"""
import collections, datetime, glob, json, os, re, sys

run = sys.argv[1]
ts = lambda l: datetime.datetime.strptime(l[:23], '%Y-%m-%d %H:%M:%S,%f').replace(tzinfo=datetime.timezone.utc).timestamp()
starts, durs, items = [], [], []
cur = None
for f in sorted(glob.glob(os.path.join(run, 'cmslog', 'EvaluationService-0', '[0-9]*.log'))):
    for l in open(f, errors='replace'):
        if 'Starting commit process' in l:
            cur, n = ts(l), 0
        elif 'Writing result to db' in l and cur:
            n += 1
        elif re.search(r'write_results(_sync)?\] Done', l) and cur:
            starts.append(cur); durs.append(ts(l) - cur); items.append(n); cur = None
u = json.load(open(os.path.join(run, 'users.json')))


def q(v, p):
    v = sorted(v)
    return v[int((len(v) - 1) * p)] if v else float('nan')


print('| window | flushes | duration p50 / p95 / max (s) | results per flush p50 / max | gap between flushes p50 / p95 / max (s) |')
print('|---|---|---|---|---|')
for name, a, b in [('whole run', 0, 1e12), ('steady', u['start'] + 60, u['stop'] - 300),
                   ('end burst + drain', u['stop'] - 300, 1e12)]:
    sel = [i for i, s in enumerate(starts) if a <= s < b]
    d = [durs[i] for i in sel]
    it = [items[i] for i in sel]
    g = [starts[i + 1] - starts[i] for i in sel if i + 1 < len(starts)]
    print('| %s | %d | %.2f / %.2f / %.2f | %d / %d | %.1f / %.1f / %.1f |' % (
        name, len(d), q(d, .5), q(d, .95), max(d), q(it, .5), max(it),
        q(g, .5), q(g, .95), max(g)))

db = json.load(open(os.path.join(run, 'db_export.json')))
subs = {s['opaque_id']: s for s in db['submissions']}
g = collections.defaultdict(list)
for line in open(os.path.join(run, 'submissions.jsonl')):
    s = json.loads(line)
    if 'opaque_id' not in s:
        continue
    x = subs[s['opaque_id']]
    kind = 'AC (any language)' if s['kind'].startswith('ac') else s['kind']
    if x['scored_at']:
        g[(s['task'], kind)].append(x['scored_at'] - x['timestamp'])
print()
print('| task | kind | n | submit->scored p50 / p95 / max (s) |')
print('|---|---|---|---|')
for k in sorted(g):
    v = g[k]
    print('| %s | %s | %d | %.1f / %.1f / %.1f |' % (k[0], k[1], len(v), q(v, .5), q(v, .95), max(v)))
