#!/usr/bin/env python3
"""Sample CMS internals every --interval seconds (run inside the cms container).

Per sample (one JSON line):
- rpc_echo_ms: round trip of the "echo" RPC to every asyncio service (ES,
  SS, ProxyService, each CWS, AWS). A slow echo means that service's event
  loop was blocked (the RPC is answered on the loop).
- queue: ES queue_status entries (count, per type) and workers busy.
- procs: per CMS process CPU seconds (cumulative) and open TCP connections
  to PostgreSQL (port 5432), from /proc.
- pg: pg_stat_activity counts by state and waiting-on-lock count.
"""

import argparse
import json
import os
import socket
import sys
import time

import psycopg2

from cms import config

SERVICES = {"ES": ("localhost", 25000), "SS": ("localhost", 28500),
            "PS": ("localhost", 28600), "AWS": ("localhost", 21100)}


def rpc(addr, method, data=None, timeout=10.0):
    t0 = time.monotonic()
    with socket.create_connection(addr, timeout=timeout) as sock:
        sock.settimeout(timeout)
        msg = {"__id": "mon", "__method": method, "__data": data or {}}
        sock.sendall(json.dumps(msg).encode() + b"\r\n")
        buf = b""
        while not buf.endswith(b"\r\n"):
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf)
    return (time.monotonic() - t0) * 1000, reply.get("__data"), \
        reply.get("__error")


def pg_socket_inodes() -> set[str]:
    inodes = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f)
                for line in f:
                    parts = line.split()
                    remote_port = int(parts[2].rsplit(":", 1)[1], 16)
                    if remote_port == 5432 and parts[3] == "01":
                        inodes.add(parts[9])
        except OSError:
            pass
    return inodes


def procs():
    pg = pg_socket_inodes()
    out = {}
    tick = os.sysconf("SC_CLK_TCK")
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().split(b"\0")
            name = None
            for i, part in enumerate(cmd):
                base = os.path.basename(part.decode(errors="replace"))
                if base.startswith("cms") and base[3:4].isupper():
                    shard = cmd[i + 1].decode() if i + 1 < len(cmd) else ""
                    name = "%s%s" % (base[3:], shard)
                    break
            if name is None:
                continue
            with open("/proc/%s/stat" % pid) as f:
                stat = f.read().rsplit(")", 1)[1].split()
            cpu = (int(stat[11]) + int(stat[12])) / tick
            rss_mb = int(stat[21]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
            conns = 0
            for fd in os.listdir("/proc/%s/fd" % pid):
                try:
                    target = os.readlink("/proc/%s/fd/%s" % (pid, fd))
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in pg:
                    conns += 1
            out[name] = {"cpu": cpu, "rss_mb": round(rss_mb, 1),
                         "pg": conns, "pid": int(pid)}
        except (OSError, IndexError, ValueError):
            continue
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--cws-count", type=int, default=2)
    args = parser.parse_args()
    services = dict(SERVICES)
    for i in range(args.cws_count):
        services["CWS%d" % i] = ("localhost", 21000 + i)
    dsn = config.database.url.replace("postgresql+psycopg2", "postgresql")
    pg_conn = None
    with open(args.out, "a") as out:
        while True:
            t = time.time()
            sample = {"t": t, "rpc_echo_ms": {}, "rpc_errors": {}}
            for name, addr in services.items():
                try:
                    ms, _data, err = rpc(addr, "echo", {"string": "x"})
                    sample["rpc_echo_ms"][name] = round(ms, 1)
                    if err:
                        sample["rpc_errors"][name] = err[:200]
                except Exception as exc:
                    sample["rpc_errors"][name] = repr(exc)[:200]
            try:
                ms, queue, _ = rpc(services["ES"], "queue_status")
                by_type = {}
                ops = 0
                for entry in queue or []:
                    item = entry.get("item", {})
                    op_type = item.get("type", "?")
                    by_type[op_type] = by_type.get(op_type, 0) + 1
                    ops += item.get("multiplicity", 1)
                sample["queue_len"] = len(queue or [])
                sample["queue_ops"] = ops
                sample["queue_by_type"] = by_type
                sample["queue_ms"] = round(ms, 1)
                ms, workers, _ = rpc(services["ES"], "workers_status")
                sample["workers_busy"] = sum(
                    1 for w in (workers or {}).values()
                    if isinstance(w.get("operations"), list)
                    and w["operations"])
                sample["workers_connected"] = sum(
                    1 for w in (workers or {}).values() if w.get("connected"))
            except Exception as exc:
                sample["rpc_errors"]["ES_queue"] = repr(exc)[:200]
            sample["procs"] = procs()
            try:
                if pg_conn is None or pg_conn.closed:
                    pg_conn = psycopg2.connect(dsn)
                    pg_conn.autocommit = True
                with pg_conn.cursor() as cur:
                    cur.execute("SELECT coalesce(state, 'none'), count(*) "
                                "FROM pg_stat_activity WHERE datname = "
                                "current_database() GROUP BY 1")
                    sample["pg_states"] = dict(cur.fetchall())
                    cur.execute("SELECT count(*) FROM pg_stat_activity "
                                "WHERE wait_event_type = 'Lock'")
                    sample["pg_lock_waits"] = cur.fetchone()[0]
                    cur.execute("SELECT max(extract(epoch FROM now() - "
                                "xact_start)) FROM pg_stat_activity WHERE "
                                "datname = current_database() AND state "
                                "LIKE 'idle in transaction%'")
                    sample["pg_max_idle_in_tx_s"] = cur.fetchone()[0]
            except Exception as exc:
                sample["rpc_errors"]["pg"] = repr(exc)[:200]
                pg_conn = None
            out.write(json.dumps(sample, default=float) + "\n")
            out.flush()
            time.sleep(max(0.0, args.interval - (time.time() - t)))


if __name__ == "__main__":
    sys.exit(main())
