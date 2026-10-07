#!/usr/bin/env python3
"""Summarize one run directory (out/<run>) into summary.md and metrics.json.

Stdlib only. Inputs: requests.jsonl, submissions.jsonl, ranking.jsonl
(driver), monitor.jsonl (monitor.py), db_export.json (db_export.py),
users.json, cmslog/ (CMS log files), db.log (PostgreSQL), docker_stats.log.
Only users.json is required: a run that crashed still gets a summary, and
metrics.json holds null for every value its missing inputs would feed.

metrics.json is one flat dict with the headline numbers of the run, the
same ones summary.md prints, for comparing runs (fork against upstream).
"""

import argparse
import collections
import glob
import json
import os
import re

PHASES = ["login_burst", "start_burst", "steady", "end_burst", "drain"]

METRIC_KEYS = (
    "run", "target", "profile", "users", "submissions_sent",
    "submissions_rejected", "submissions_rejected_in_time",
    "login_failures", "http_errors", "score_mismatches", "rws_pairs",
    "rws_mismatches", "login_p50", "login_p95", "submit_p50", "submit_p95",
    "submit_end_p95", "scored_p50", "scored_p95", "scored_max",
    "drain_after_stop_s", "peak_pg_connections", "cpu_mean_by_container",
    "mem_last_by_container")


def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def fmt(x, digits=3):
    if x != x:  # NaN
        return "-"
    return ("%%.%df" % digits) % x


def metric(x: float | None, digits: int) -> float | None:
    """Return x rounded to digits for metrics.json, or None if x is NaN."""
    if x is None or x != x:
        return None
    return round(x, digits)


def load_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


def rws_mismatches(task_scores: list[dict], ranking: list[dict],
                   ranked: set[str]) -> tuple[int, list]:
    """Compare the final RWS scores with the CMS task scores.

    task_scores: db_export task_scores entries (contest, user, task, score).
    ranking: driver ranking.jsonl entries (user, task, score, in time order).
    ranked: names of the contests the RWS publishes; the others are skipped.

    return: the number of (user, task) pairs checked and the list of
        (task_score, rws_score) pairs that differ.

    """
    final_rws = {}
    for r in ranking:
        final_rws[(r["user"], r["task"])] = r["score"]
    pairs = 0
    diff = []
    for ts in task_scores:
        if ts["contest"] not in ranked:
            continue
        pairs += 1
        cms_score = round(ts["score"], 6)
        rws_score = round(final_rws.get((ts["user"], ts["task"]), 0.0), 6)
        if abs(cms_score - rws_score) > 1e-6:
            diff.append((ts, rws_score))
    return pairs, diff


def main(run_dir: str, project_prefix: str = "cmsload-") -> None:
    """Write summary.md and metrics.json for the run in run_dir.

    run_dir: the run directory (out/<run>).
    project_prefix: only docker_stats.log containers whose name starts
        with it are reported (the compose project name).

    """
    lines = []
    w = lines.append
    users = json.load(open(os.path.join(run_dir, "users.json")))
    start, stop = users["start"], users["stop"]
    reqs = load_jsonl(os.path.join(run_dir, "requests.jsonl"))
    subs = load_jsonl(os.path.join(run_dir, "submissions.jsonl"))
    ranking = load_jsonl(os.path.join(run_dir, "ranking.jsonl"))
    mon = load_jsonl(os.path.join(run_dir, "monitor.jsonl"))
    db_path = os.path.join(run_dir, "db_export.json")
    have_db = os.path.exists(db_path)
    if have_db:
        with open(db_path) as f:
            db = json.load(f)
    else:  # the run crashed before the export
        db = {"submissions": [], "task_scores": []}

    # Every value of metrics.json is set next to where summary.md prints it.
    metrics = dict.fromkeys(METRIC_KEYS)
    metrics["run"] = os.path.basename(run_dir.rstrip("/"))
    metrics["target"] = users.get("target")
    metrics["profile"] = users.get("profile")
    metrics["users"] = len(users["users"])

    w("# Run %s\n" % metrics["run"])
    w("Target %s, profile %s." % (metrics["target"], metrics["profile"]))
    w("Users: %d (loada %d, loadb %d). Contest %.0f s; start %s."
      % (len(users["users"]),
         sum(1 for u in users["users"] if u["contest"] == "loada"),
         sum(1 for u in users["users"] if u["contest"] == "loadb"),
         stop - start, start))

    # ---- HTTP requests ----------------------------------------------------
    w("\n## HTTP latency by request kind and phase (seconds)\n")
    w("| kind | phase | n | errors | p50 | p95 | p99 | max |")
    w("|---|---|---|---|---|---|---|---|")
    groups = collections.defaultdict(list)
    errors = collections.Counter()
    status_counts = collections.Counter()
    for r in reqs:
        groups[(r["kind"], r["phase"])].append(r["dur"])
        if not r.get("ok"):
            errors[(r["kind"], r["phase"])] += 1
            status_counts[(r["kind"], r.get("status"),
                           (r.get("error") or "")[:80])] += 1
    kinds = sorted({k for k, _ in groups})
    for kind in kinds:
        for phase in PHASES:
            d = groups.get((kind, phase))
            if not d:
                continue
            w("| %s | %s | %d | %d | %s | %s | %s | %s |" % (
                kind, phase, len(d), errors[(kind, phase)], fmt(pct(d, 50)),
                fmt(pct(d, 95)), fmt(pct(d, 99)), fmt(max(d))))
    metrics["http_errors"] = sum(errors.values())
    login_d = [r["dur"] for r in reqs if r["kind"] == "login"]
    submit_d = [r["dur"] for r in reqs if r["kind"] == "submit"]
    submit_end_d = [r["dur"] for r in reqs
                    if r["kind"] == "submit" and r["phase"] == "end_burst"]
    head = {"login_p50": pct(login_d, 50), "login_p95": pct(login_d, 95),
            "submit_p50": pct(submit_d, 50), "submit_p95": pct(submit_d, 95),
            "submit_end_p95": pct(submit_end_d, 95)}
    for key, value in head.items():
        metrics[key] = metric(value, 3)
    w("\nAll phases: login p50 %s s, p95 %s s; submit p50 %s s, p95 %s s; "
      "submit in end_burst p95 %s s." % (
          fmt(head["login_p50"]), fmt(head["login_p95"]),
          fmt(head["submit_p50"]), fmt(head["submit_p95"]),
          fmt(head["submit_end_p95"])))
    if status_counts:
        w("\nFailed requests by (kind, status, error):\n")
        for (kind, status, err), n in status_counts.most_common(30):
            w("- %s status=%s %s: %d" % (kind, status, err, n))
    rps = collections.Counter(int(r["t"]) for r in reqs
                              if r["kind"] != "rws_scores")
    if rps:
        w("\nPeak CWS request rate: %d req/s (1 s buckets); mean %.1f req/s."
          % (max(rps.values()), sum(rps.values()) / max(1, len(rps))))

    # ---- submissions --------------------------------------------------------
    w("\n## Submissions\n")
    by_opaque = {s["opaque_id"]: s for s in db["submissions"]}
    driver_subs = [s for s in subs if "kind" in s]
    login_failed = [s for s in subs if s.get("login_failed")]
    if not have_db:
        w("db_export.json is missing: nothing below can be checked "
          "against the database.\n")
    w("Login failures (gave up after 5 tries): %d" % len(login_failed))
    rejected = [s for s in driver_subs if not s.get("accepted")]
    # A POST created at or after the stop is rejected by design; only the
    # ones created before it say something about the server.
    rejected_in_time = [s for s in rejected if s["t_submit"] < stop]
    w("Submissions sent: %d; rejected by CWS: %d (%d created before the "
      "stop); DB rows: %d."
      % (len(driver_subs), len(rejected), len(rejected_in_time),
         len(db["submissions"])))
    metrics["login_failures"] = len(login_failed)
    metrics["submissions_sent"] = len(driver_subs)
    metrics["submissions_rejected"] = len(rejected)
    metrics["submissions_rejected_in_time"] = len(rejected_in_time)
    for s in rejected[:10]:
        w("  - rejected %s %s status=%s location=%s" % (
            s["user"], s["task"], s.get("status"), s.get("location")))
    w("\n| phase | sent | server submit->scored p50 | p95 | max | "
      "perceived (browser backoff) p50 | p95 | max |")
    w("|---|---|---|---|---|---|---|---|")
    stuck, mismatches, unidentified = [], [], []
    per_phase = collections.defaultdict(lambda: ([], [], 0))
    for s in driver_subs:
        if not s.get("accepted"):
            continue
        if "opaque_id" not in s:
            unidentified.append(s)
            continue
        server = by_opaque.get(s["opaque_id"])
        srv_lat = None
        if server and server.get("scored_at"):
            srv_lat = server["scored_at"] - server["timestamp"]
        perceived = None
        if s.get("t_terminal_seen"):
            perceived = s["t_terminal_seen"] - s["t_submit"]
        a, b, n = per_phase[s["phase"]]
        if srv_lat is not None:
            a.append(srv_lat)
        if perceived is not None:
            b.append(perceived)
        per_phase[s["phase"]] = (a, b, n + 1)
        if s.get("final_status") == "stuck" or server is None \
                or server.get("scored_at") is None:
            stuck.append((s, server))
        elif server["score"] is not None and \
                abs(server["score"] - s["expected"]) > 1e-6:
            mismatches.append((s, server))
    all_srv = []
    for phase in PHASES:
        if phase not in per_phase:
            continue
        a, b, n = per_phase[phase]
        all_srv += a
        w("| %s | %d | %s | %s | %s | %s | %s | %s |" % (
            phase, n, fmt(pct(a, 50), 1), fmt(pct(a, 95), 1),
            fmt(max(a) if a else float("nan"), 1), fmt(pct(b, 50), 1),
            fmt(pct(b, 95), 1), fmt(max(b) if b else float("nan"), 1)))
    scored = {"scored_p50": pct(all_srv, 50), "scored_p95": pct(all_srv, 95),
              "scored_max": max(all_srv) if all_srv else float("nan")}
    for key, value in scored.items():
        metrics[key] = metric(value, 1)
    w("\nAll: server submit->scored p50 %s s, p95 %s s, max %s s."
      % (fmt(scored["scored_p50"], 1), fmt(scored["scored_p95"], 1),
         fmt(scored["scored_max"], 1)))
    scored_times = [s["scored_at"] for s in db["submissions"]
                    if s.get("scored_at")]
    if scored_times:
        metrics["drain_after_stop_s"] = metric(max(scored_times) - stop, 0)
        w("Last submission scored %.0f s after the contest stop."
          % metrics["drain_after_stop_s"])
    w("\nNever scored (stuck): %d" % len(stuck))
    for s, server in stuck[:20]:
        w("  - %s %s %s opaque=%s server=%s" % (
            s["user"], s["task"], s["kind"], s.get("opaque_id"),
            json.dumps(server)))
    w("Rows not identified on the submissions page: %d" % len(unidentified))
    w("Score mismatches vs expected: %d" % len(mismatches))
    if have_db:
        metrics["score_mismatches"] = len(mismatches)
    for s, server in mismatches[:20]:
        w("  - %s %s %s expected %s got %s (id %s, evaluations %s)" % (
            s["user"], s["task"], s["kind"], s["expected"], server["score"],
            server["id"], server["evaluations"]))
    kinds_c = collections.Counter(s["kind"] for s in driver_subs)
    w("Mix: %s" % dict(kinds_c))
    tries = collections.Counter(
        (s["compilation_tries"], s["evaluation_tries"])
        for s in db["submissions"])
    w("(compilation_tries, evaluation_tries) histogram: %s" % dict(tries))

    # ---- ranking ------------------------------------------------------------
    w("\n## Ranking (RWS)\n")
    ranked = set(users["ranked"])
    pairs, diff = rws_mismatches(db["task_scores"], ranking, ranked)
    if have_db:
        metrics["rws_pairs"] = pairs
        metrics["rws_mismatches"] = len(diff)
    w("Final RWS vs CMS task scores in the ranked contests (%s): "
      "%d (user, task) pairs, %d differ."
      % (", ".join(sorted(ranked)), pairs, len(diff)))
    for ts, rws_score in diff[:20]:
        w("  - %s %s CMS %s RWS %s partial=%s" % (
            ts["user"], ts["task"], ts["score"], rws_score, ts["partial"]))
    scored_by_ut = collections.defaultdict(list)
    for s in db["submissions"]:
        if s.get("scored_at"):
            scored_by_ut[(s["user"], s["task"])].append(s["scored_at"])
    lags = []
    for r in ranking:
        cands = [t for t in scored_by_ut.get((r["user"], r["task"]), [])
                 if t <= r["t"]]
        if cands:
            lags.append(r["t"] - max(cands))
    w("Ranking lag (RWS change seen - latest scored_at; 2 s polling): "
      "n=%d p50 %s p95 %s max %s s" % (
          len(lags), fmt(pct(lags, 50), 1), fmt(pct(lags, 95), 1),
          fmt(max(lags) if lags else float("nan"), 1)))

    # ---- monitor ------------------------------------------------------------
    w("\n## Internals (monitor, 2 s samples)\n")
    if mon:
        qo = [m.get("queue_ops", 0) for m in mon if "queue_ops" in m]
        w("ES queue: max %d operations (max %d distinct entries); "
          "workers busy mean %.1f, max %d of %d." % (
              max(qo or [0]),
              max([m.get("queue_len", 0) for m in mon] or [0]),
              sum(m.get("workers_busy", 0) for m in mon) / len(mon),
              max(m.get("workers_busy", 0) for m in mon),
              max(m.get("workers_connected", 0) for m in mon)))
        w("\nQueue per minute (max ops / mean busy workers):")
        per_min = collections.defaultdict(list)
        for m in mon:
            per_min[int((m["t"] - start) // 60)].append(m)
        row = []
        for minute in sorted(per_min):
            ms = per_min[minute]
            row.append("%+d:%d/%.1f" % (
                minute, max(x.get("queue_ops", 0) for x in ms),
                sum(x.get("workers_busy", 0) for x in ms) / len(ms)))
        w(" ".join(row))
        w("\n| service | echo p50 ms | p99 ms | max ms | samples >250 ms | "
          ">1 s | RPC errors |")
        w("|---|---|---|---|---|---|---|")
        names = sorted({k for m in mon for k in m.get("rpc_echo_ms", {})})
        for name in names:
            v = [m["rpc_echo_ms"][name] for m in mon
                 if name in m.get("rpc_echo_ms", {})]
            errs = sum(1 for m in mon if name in m.get("rpc_errors", {}))
            w("| %s | %s | %s | %s | %d | %d | %d |" % (
                name, fmt(pct(v, 50), 1), fmt(pct(v, 99), 1),
                fmt(max(v), 1), sum(1 for x in v if x > 250),
                sum(1 for x in v if x > 1000), errs))
        err_examples = collections.Counter()
        for m in mon:
            for k, e in m.get("rpc_errors", {}).items():
                err_examples[(k, e[:100])] += 1
        for (k, e), n in err_examples.most_common(10):
            w("- monitor RPC error %s: %s (x%d)" % (k, e, n))
        w("\n| process | max PG conns | CPU s during run | max RSS MB |")
        w("|---|---|---|---|")
        procs = sorted({k for m in mon for k in m.get("procs", {})})
        for p in procs:
            samples = [m["procs"][p] for m in mon if p in m.get("procs", {})]
            w("| %s | %d | %.1f | %.0f |" % (
                p, max(s["pg"] for s in samples),
                samples[-1]["cpu"] - samples[0]["cpu"],
                max(s["rss_mb"] for s in samples)))
        pg_total = [sum(m["pg_states"].values()) for m in mon
                    if "pg_states" in m]
        metrics["peak_pg_connections"] = max(pg_total, default=None)
        w("\nPostgreSQL backends for cmsdb: max %d (max_connections 100); "
          "max 'active' %d; max lock waits %d; longest idle-in-transaction "
          "%s s." % (max(pg_total or [0]),
                     max(m.get("pg_states", {}).get("active", 0)
                         for m in mon),
                     max(m.get("pg_lock_waits", 0) for m in mon),
                     fmt(max((m.get("pg_max_idle_in_tx_s") or 0)
                             for m in mon), 1)))

    # ---- logs ---------------------------------------------------------------
    w("\n## Service logs\n")
    patterns = {
        "Traceback": r"Traceback",
        "No pending request": r"No pending request",
        "timeout/timed out": r"(?i)time(d)? ?out",
        "QueuePool": r"QueuePool",
        "TimeoutError": r"TimeoutError",
        "IntegrityError": r"(?i)integrity ?error",
        "Unexpected error": r"Unexpected error",
        "missed operation(s)": r"Found \d+ missed operation",
        "gates holding": r"gates are holding each other",
        "too many clients": r"too many clients",
        "put again in the queue": r"put again in the queue",
        "cannot be used": r"cannot be used",
        "Failed reading/writing": r"Failed (reading|writing)",
        "RPC unsuccessful": r"signaled RPC for method",
        "Ignored result": r"Ignored result|result ignored",
        "gave no answer": r"gave no answer",
    }
    log_files = sorted(glob.glob(os.path.join(run_dir, "cmslog", "**", "*"),
                                 recursive=True))
    log_files = [f for f in log_files if os.path.isfile(f)
                 and not os.path.islink(f)]
    level_counts = collections.Counter()
    pattern_counts = collections.Counter()
    messages = collections.Counter()
    examples = {}
    level_re = re.compile(r" - (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
    missed = collections.Counter()
    for path in log_files:
        service = os.path.relpath(path, os.path.join(run_dir, "cmslog")) \
            .split(os.sep)[0]
        with open(path, errors="replace") as f:
            for line in f:
                m = level_re.search(line)
                if m:
                    level = m.group(1)
                    level_counts[(service, level)] += 1
                    if level in ("WARNING", "ERROR", "CRITICAL"):
                        msg = line[m.end():].strip()
                        norm = re.sub(r"\d+", "N", msg)[:160]
                        messages[(service, level, norm)] += 1
                        examples.setdefault((service, level, norm),
                                            line.strip()[:400])
                for name, rx in patterns.items():
                    if re.search(rx, line):
                        pattern_counts[(service, name)] += 1
                mm = re.search(r"Found (\d+) missed operation", line)
                if mm:
                    missed[service] += int(mm.group(1))
    w("Log files: %d" % len(log_files))
    w("\n| service | WARNING | ERROR | CRITICAL |")
    w("|---|---|---|---|")
    services = sorted({s for s, _ in level_counts})
    for s in services:
        w("| %s | %d | %d | %d |" % (s, level_counts[(s, "WARNING")],
                                     level_counts[(s, "ERROR")],
                                     level_counts[(s, "CRITICAL")]))
    w("\nPattern hits:")
    for (s, name), n in sorted(pattern_counts.items()):
        w("- %s: %s x%d" % (s, name, n))
    for s, n in missed.items():
        w("- %s: sweeper found %d missed operations in total" % (s, n))
    w("\nDistinct WARNING/ERROR messages (normalized digits):")
    for key, n in messages.most_common(40):
        w("- x%d %s %s: `%s`" % (n, key[0], key[1], examples[key]))

    db_log = os.path.join(run_dir, "db.log")
    if os.path.exists(db_log):
        slow = collections.Counter()
        other = collections.Counter()
        for line in open(db_log, errors="replace"):
            if "duration:" in line:
                slow[re.sub(r"\d+(\.\d+)?", "N", line.split("statement:")[-1]
                            .strip())[:120]] += 1
            elif re.search(r"ERROR|FATAL|deadlock|still waiting", line):
                other[re.sub(r"\d+", "N", line.split(" UTC ")[-1].strip())
                      [:160]] += 1
        w("\n## PostgreSQL log\n")
        w("Slow statements (>1 s): %d" % sum(slow.values()))
        for k, n in slow.most_common(10):
            w("- x%d %s" % (n, k))
        w("Errors/lock waits: %d" % sum(other.values()))
        for k, n in other.most_common(15):
            w("- x%d %s" % (n, k))

    stats_path = os.path.join(run_dir, "docker_stats.log")
    if os.path.exists(stats_path):
        cpu = collections.defaultdict(list)
        mem = collections.defaultdict(list)
        for line in open(stats_path):
            ts, _, js = line.partition(" ")
            try:
                d = json.loads(js)
            except ValueError:
                continue
            if not d.get("Name", "").startswith(project_prefix):
                continue
            cpu[d["Name"]].append(float(d["CPUPerc"].rstrip("%")))
            mem[d["Name"]].append(d["MemUsage"].split("/")[0].strip())
        w("\n## Containers (docker stats, 5 s)\n")
        for name in sorted(cpu):
            w("- %s: CPU mean %.0f%% max %.0f%% (100%% = 1 core); last mem %s"
              % (name, sum(cpu[name]) / len(cpu[name]), max(cpu[name]),
                 mem[name][-1]))
        if cpu:
            metrics["cpu_mean_by_container"] = {
                name: round(sum(v) / len(v), 1) for name, v in cpu.items()}
            metrics["mem_last_by_container"] = {
                name: v[-1] for name, v in mem.items()}

    with open(os.path.join(run_dir, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
    print("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_dir", help="the run directory (out/<run>)")
    parser.add_argument("--project-prefix", default="cmsload-",
                        help="report the docker stats of the containers "
                        "whose name starts with this (compose project)")
    cli = parser.parse_args()
    main(cli.run_dir, cli.project_prefix)
