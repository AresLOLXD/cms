#!/usr/bin/env python3
"""Contest-shaped load driver for CMS ContestWebServer (multi-contest).

Simulates contestants with a browser-like behaviour against CWS:

1. Login burst: every user arrives during the --login-window seconds
   before the start (60% of them in the last third), loads the contest
   page, logs in with the form (bcrypt check on the server) and loads the
   page again; then polls /notifications every 30 s like contest.html.
2. Start burst: within 20 s of the start every user reloads the contest
   page, opens 2-3 task descriptions and downloads statements.
3. Steady phase: closed loop per user: think (exponential, mean chosen from
   --steady-rate), sometimes browse, submit, poll the submission status
   with the backoff of task_submissions.html until it is terminal, maybe
   open the details.
4. End burst: in the last --end-burst seconds every user submits 1-3 more
   times (skewed towards the stop), without waiting for results.
5. Drain: after the stop, the pending polls continue until every submission
   is terminal or --drain-timeout passes.

A ranking watcher polls the RWS scores path of every ranked contest (the
"ranked" map of users.json; it depends on the profile) every 2 s.

Every request goes to the CWS shards round-robin (like an nginx upstream).
Output (in --out): requests.jsonl, submissions.jsonl, ranking.jsonl,
progress.log. Needs only aiohttp; it can run on any client machine
against a remote host (see README in REPORT.md).
"""

import argparse
import asyncio
import itertools
import json
import os
import random
import re
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenario  # noqa: E402

SOLUTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "solutions")
TERMINAL = {2, 5}  # COMPILATION_FAILED, SCORED
SUBMISSION_ROW_RE = re.compile(rb'data-submission="(\d+)"')


class Recorder:
    def __init__(self, out_dir: str, start: float, stop: float,
                 end_burst: float):
        self.start, self.stop, self.end_burst = start, stop, end_burst
        self.req_file = open(os.path.join(out_dir, "requests.jsonl"), "w")
        self.sub_file = open(os.path.join(out_dir, "submissions.jsonl"), "w")
        self.rank_file = open(os.path.join(out_dir, "ranking.jsonl"), "w")
        self.counters: dict[str, int] = {}

    def phase(self, t: float) -> str:
        if t < self.start:
            return "login_burst"
        if t < self.start + 60:
            return "start_burst"
        if t < self.stop - self.end_burst:
            return "steady"
        if t < self.stop:
            return "end_burst"
        return "drain"

    def request(self, **entry):
        entry["phase"] = self.phase(entry["t"])
        self.req_file.write(json.dumps(entry) + "\n")
        key = "%s:%s" % (entry["kind"], "ok" if entry.get("ok") else "bad")
        self.counters[key] = self.counters.get(key, 0) + 1

    def submission(self, **entry):
        self.sub_file.write(json.dumps(entry) + "\n")
        self.sub_file.flush()

    def ranking(self, **entry):
        self.rank_file.write(json.dumps(entry) + "\n")

    def close(self):
        for f in (self.req_file, self.sub_file, self.rank_file):
            f.close()


class Driver:
    def __init__(self, args, data):
        self.args = args
        self.start = data["start"]
        self.stop = data["stop"]
        self.users = data["users"]
        self.profile = data["profile"]
        self.ranked = data["ranked"]
        self.bases = [b.rstrip("/") for b in args.cws]
        self.rr = itertools.cycle(range(len(self.bases)))
        self.rec = Recorder(args.out, self.start, self.stop, args.end_burst)
        self.pending_polls: set[asyncio.Task] = set()
        self.submitted = 0
        self.terminal = 0
        self.rng = random.Random(args.seed)
        self.sources = {}
        for kind, (fname, _lang, _w, _p) in scenario.SOLUTIONS.items():
            with open(os.path.join(SOLUTIONS_DIR, fname), "rb") as f:
                self.sources[kind] = f.read()
        self.kinds = list(scenario.SOLUTIONS)
        self.weights = [scenario.SOLUTIONS[k][2] for k in self.kinds]
        n = len(self.users)
        # Mean think time so that n users give --steady-rate submissions/min
        # (ignoring the time spent waiting for results).
        self.mean_think = n * 60.0 / args.steady_rate

    # ---- HTTP -----------------------------------------------------------

    async def http(self, user, method, path, kind, *, expect=(200,),
                   data=None, params=None):
        base = self.bases[next(self.rr)]
        url = "%s/%s%s" % (base, user["contest"], path)
        t = time.time()
        t0 = time.monotonic()
        status, body, location, error = -1, b"", None, None
        try:
            async with user["session"].request(
                    method, url, data=data, params=params,
                    allow_redirects=False) as resp:
                body = await resp.read()
                status = resp.status
                location = resp.headers.get("Location")
        except Exception as exc:  # timeouts, resets, ...
            error = "%s: %s" % (type(exc).__name__, exc)
        dur = time.monotonic() - t0
        ok = status in expect
        entry = dict(t=t, kind=kind, dur=round(dur, 4), status=status,
                     ok=ok, user=user["username"],
                     shard=url.split("/")[2])
        if error:
            entry["error"] = error[:300]
        elif not ok:
            entry["body"] = body[:300].decode("utf-8", "replace")
        self.rec.request(**entry)
        return status, body, location

    def xsrf(self, user) -> str:
        for cookie in user["session"].cookie_jar:
            if cookie.key == "_xsrf":
                return cookie.value
        return ""

    # ---- behaviour ------------------------------------------------------

    async def sleep_until(self, t: float):
        delay = t - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

    async def login(self, user) -> bool:
        await self.http(user, "GET", "/", "home_anon")
        for attempt in range(5):
            status, _body, location = await self.http(
                user, "POST", "/login", "login", expect=(302,),
                data={"username": user["username"],
                      "password": user["password"],
                      "next": "/%s/" % user["contest"],
                      "_xsrf": self.xsrf(user)})
            if status == 302 and location and "login_error" not in location:
                status, body, _ = await self.http(user, "GET", "/",
                                                  "home_logged")
                if user["username"].encode() in body:
                    return True
            await asyncio.sleep(2 + attempt * 3)
        return False

    async def notifications_loop(self, user):
        await asyncio.sleep(self.rng.uniform(0, 30))
        while time.time() < self.stop + self.args.drain_timeout \
                and not user.get("done"):
            await self.http(user, "GET", "/notifications", "notifications")
            await asyncio.sleep(30)

    async def browse_tasks(self, user, count: int):
        tasks = self.rng.sample(user["tasks"], min(count, len(user["tasks"])))
        for task in tasks:
            await self.http(user, "GET", "/tasks/%s/description" % task,
                            "task_description")
            if self.rng.random() < 0.7:
                await self.http(user, "GET", "/tasks/%s/statements/es" % task,
                                "statement")
            await asyncio.sleep(self.rng.uniform(1, 5))

    async def submit(self, user, wait: bool):
        task = self.rng.choice(user["tasks"])
        kind = self.rng.choices(self.kinds, self.weights)[0]
        fname, lang_key, _w, _p = scenario.SOLUTIONS[kind]
        source = self.sources[kind]
        ext = {"cpp": "cpp", "py": "py", "java": "java"}[lang_key]
        if lang_key == "java":
            source = source.replace(b"TASKNAME", task.encode())
        if self.rng.random() < 0.3:
            await self.http(user, "GET", "/tasks/%s/description" % task,
                            "task_description")
        form = aiohttp.FormData()
        form.add_field("_xsrf", self.xsrf(user))
        form.add_field("language", scenario.LANGUAGES[lang_key])
        form.add_field("%s.%%l" % task, source,
                       filename="%s.%s" % (task, ext),
                       content_type="application/octet-stream")
        t_submit = time.time()
        status, _body, location = await self.http(
            user, "POST", "/tasks/%s/submit" % task, "submit",
            expect=(302,), data=form)
        accepted = bool(location and "submission_id=" in location)
        entry = dict(user=user["username"], contest=user["contest"],
                     task=task, kind=kind, t_submit=t_submit,
                     phase=self.rec.phase(t_submit), accepted=accepted,
                     expected=scenario.expected_score(
                         task, kind, self.profile))
        if not accepted:
            entry["location"] = location
            entry["status"] = status
            self.rec.submission(**entry)
            return
        self.submitted += 1
        # The browser follows the redirect to the submissions page.
        path = location.split("/%s" % user["contest"], 1)[-1]
        known = user["known"].setdefault(task, set())
        status, body, _ = await self.http(user, "GET", path,
                                          "submissions_page")
        ids = {int(x) for x in SUBMISSION_ROW_RE.findall(body)}
        new = ids - known
        known.update(ids)
        if len(new) != 1:
            entry["error"] = "cannot identify the new row (%d new)" % len(new)
            self.rec.submission(**entry)
            return
        entry["opaque_id"] = new.pop()
        poll = asyncio.create_task(self.poll(user, entry))
        self.pending_polls.add(poll)
        poll.add_done_callback(self.pending_polls.discard)
        if wait:
            await poll

    async def poll(self, user, entry):
        """Poll like task_submissions.html: 1 s, then x(1.4 + hash*0.2)."""
        opaque = entry["opaque_id"]
        factor = 1.4 + ((37 * opaque) % 100) / 100.0 * 0.2
        delay = 1.0
        polls = 0
        deadline = self.stop + self.args.drain_timeout
        while True:
            await asyncio.sleep(delay)
            polls += 1
            status, body, _ = await self.http(
                user, "GET", "/tasks/%s/submissions/%d" % (entry["task"],
                                                           opaque),
                "status_poll")
            if status == 200:
                try:
                    data = json.loads(body)
                except ValueError:
                    data = {}
                if data.get("status") in TERMINAL:
                    entry["t_terminal_seen"] = time.time()
                    entry["final_status"] = data["status"]
                    entry["public_score"] = data.get("public_score")
                    entry["polls"] = polls
                    self.terminal += 1
                    break
            if time.time() > deadline:
                entry["final_status"] = "stuck"
                entry["polls"] = polls
                break
            delay *= factor
            # Never poll past the drain deadline by much.
            delay = min(delay, max(1.0, deadline - time.time()))
        self.rec.submission(**entry)
        if entry.get("final_status") in TERMINAL and self.rng.random() < 0.5:
            await self.http(user, "GET", "/tasks/%s/submissions/%d/details"
                            % (entry["task"], opaque), "submission_details")

    async def user_main(self, user):
        arrival = user["arrival"]
        await self.sleep_until(arrival)
        if not await self.login(user):
            self.rec.submission(user=user["username"], login_failed=True)
            return
        notif = asyncio.create_task(self.notifications_loop(user))
        # Start burst: reload when the contest starts.
        await self.sleep_until(self.start + self.rng.uniform(0.5, 20))
        await self.http(user, "GET", "/", "contest_page")
        await self.browse_tasks(user, self.rng.randint(2, 3))
        end_burst_start = self.stop - self.args.end_burst
        # Steady phase (closed loop).
        while True:
            think = self.rng.expovariate(1.0 / self.mean_think)
            if time.time() + think >= end_burst_start:
                break
            await asyncio.sleep(think)
            if self.rng.random() < 0.3:
                page = self.rng.choice(["contest_page", "submissions_list"])
                if page == "contest_page":
                    await self.http(user, "GET", "/", "contest_page")
                else:
                    task = self.rng.choice(user["tasks"])
                    await self.http(user, "GET", "/tasks/%s/submissions"
                                    % task, "submissions_list")
            if time.time() >= end_burst_start:
                break
            await self.submit(user, wait=True)
        # End burst (open loop): 1-3 submissions, skewed to the stop.
        count = self.rng.choices([1, 2, 3], [0.3, 0.4, 0.3])[0]
        count = max(0, round(count * self.args.end_burst_factor))
        times = sorted(self.stop - 3 - self.args.end_burst *
                       (self.rng.random() ** 1.7) for _ in range(count))
        for t in times:
            await self.sleep_until(max(t, end_burst_start))
            if time.time() < self.stop - 2:
                await self.submit(user, wait=False)
        user["done_submitting"] = True
        await notif

    async def ranking_watcher(self, session):
        last: dict[tuple, float] = {}
        deadline = self.stop + self.args.drain_timeout + 60
        while time.time() < deadline and not self.finished:
            for contest, path in self.ranked.items():
                t = time.time()
                t0 = time.monotonic()
                try:
                    async with session.get(
                            "%s/%s" % (self.args.rws.rstrip("/"), path),
                            headers={"Accept": "application/json"}) as resp:
                        scores = await resp.json(content_type=None)
                        status = resp.status
                except Exception as exc:
                    self.rec.request(t=t, kind="rws_scores", ok=False,
                                     dur=time.monotonic() - t0, status=-1,
                                     user="-", shard="rws", error=str(exc))
                    continue
                self.rec.request(t=t, kind="rws_scores", ok=status == 200,
                                 dur=round(time.monotonic() - t0, 4),
                                 status=status, user="-", shard="rws")
                for u, tasks in (scores or {}).items():
                    for task, score in tasks.items():
                        if last.get((u, task)) != score:
                            last[(u, task)] = score
                            self.rec.ranking(t=t, user=u, task=task,
                                             score=score, contest=contest)
            await asyncio.sleep(2)

    async def progress(self):
        path = os.path.join(self.args.out, "progress.log")
        with open(path, "w") as f:
            while not self.finished:
                line = "%s phase=%s submitted=%d terminal=%d polls=%d %s" % (
                    time.strftime("%H:%M:%S"), self.rec.phase(time.time()),
                    self.submitted, self.terminal, len(self.pending_polls),
                    json.dumps(self.rec.counters, sort_keys=True))
                f.write(line + "\n")
                f.flush()
                print(line, flush=True)
                await asyncio.sleep(10)

    async def run(self):
        self.finished = False
        login_window = self.args.login_window
        for i, user in enumerate(self.users):
            if self.rng.random() < 0.6:
                offset = self.rng.uniform(0, login_window / 3)
            else:
                offset = self.rng.uniform(login_window / 3, login_window)
            user["arrival"] = self.start - 5 - offset
            user["tasks"] = [t["name"] for t in scenario.TASKS[user["contest"]]]
            user["known"] = {}
        connector_limit = self.args.connections
        timeout = aiohttp.ClientTimeout(total=self.args.request_timeout)
        sessions = []
        for user in self.users:
            s = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                connector=aiohttp.TCPConnector(limit=connector_limit),
                timeout=timeout)
            user["session"] = s
            sessions.append(s)
        rws_session = aiohttp.ClientSession(timeout=timeout)
        watchers = [asyncio.create_task(self.ranking_watcher(rws_session)),
                    asyncio.create_task(self.progress())]
        try:
            users = [asyncio.create_task(self.user_main(u))
                     for u in self.users]
            # Users finish at stop + drain_timeout (notifications loops).
            done_users = asyncio.gather(*users, return_exceptions=True)
            while True:
                await asyncio.sleep(2)
                now = time.time()
                if now > self.stop and not self.pending_polls:
                    break
                if now > self.stop + self.args.drain_timeout + 30:
                    break
            for u in self.users:
                u["done"] = True
            await asyncio.sleep(self.args.ranking_tail)
            self.finished = True
            for task in users:
                task.cancel()
            results = await done_users
            for r in results:
                if isinstance(r, Exception) and \
                        not isinstance(r, asyncio.CancelledError):
                    print("user task failed: %r" % r, file=sys.stderr)
        finally:
            self.finished = True
            for w in watchers:
                w.cancel()
            for s in sessions:
                await s.close()
            await rws_session.close()
            self.rec.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--users-file", required=True,
                        help="users.json written by setup_contest.py")
    parser.add_argument("--cws", nargs="+", required=True,
                        help="CWS base URLs (one per shard or one balancer)")
    parser.add_argument("--rws", required=True, help="RWS base URL")
    parser.add_argument("--out", required=True)
    parser.add_argument("--login-window", type=float, default=120)
    parser.add_argument("--end-burst", type=float, default=300)
    parser.add_argument("--end-burst-factor", type=float, default=1.0)
    parser.add_argument("--steady-rate", type=float, default=45,
                        help="target submissions per minute, steady phase")
    parser.add_argument("--drain-timeout", type=float, default=1200)
    parser.add_argument("--ranking-tail", type=float, default=20)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--connections", type=int, default=6,
                        help="max parallel connections per user (browser)")
    parser.add_argument("--limit-users", type=int, default=0)
    parser.add_argument("--seed", type=int, default=1010)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    with open(args.users_file) as f:
        data = json.load(f)
    if args.limit_users:
        data["users"] = data["users"][:args.limit_users]
    if data["start"] - time.time() < 10:
        print("The contest starts too soon (or started): %.0f s"
              % (data["start"] - time.time()))
    asyncio.run(Driver(args, data).run())


if __name__ == "__main__":
    main()
