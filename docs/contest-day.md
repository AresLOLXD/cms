# Contest day

A checklist for the people who run a contest on a Docker deployment of this
fork, from the week before to the backup afterwards. It does not repeat the
other guides: each step links to the page that explains it.

The commands run on the server, from the repository root. Where a command
needs Docker Compose directly, `<project>` is `CMS_PROJECT_NAME` from `.env`
(`cms-prod` if it is unset).

## The week before

### Size the deployment

All the simultaneous contests share one queue and the same workers. The
numbers below come from a load test with 250 simulated contestants on one
server; use them as a starting point, not as a limit.

- **Workers:** `CMS_WORKER_COUNT` (default 1). The load test used 8. With
  fewer, the queue takes longer to drain after the contest ends (see "At the
  end").
- **Contest Web Server processes:** `CMS_CWS_COUNT` (default 1). With 2, a
  server under heavy load rejected some submissions made in the last seconds;
  with 4, none (see "At the end" for why). With the request time header on
  (see "Submissions at the contest stop" in
  [docker-deployment.md](docker-deployment.md)), a busy server no longer
  refuses a submission that reached the reverse proxy before the stop.
  Without it, keep 4 processes.
- **Ports:** process *i* listens on `CMS_CWS_HTTP_PORT` + *i*. Nothing checks
  that this range stays clear of `CMS_AWS_HTTP_PORT` and `CMS_RWS_HTTP_PORT`,
  and with the defaults (8888, 8889, 8890) a second process lands on the
  Admin port. Move the Admin and Ranking ports above the range, and balance
  the processes with `ip_hash` in the reverse proxy. See "Scaling Contest Web
  Server" in [docker-scripts.md](docker-scripts.md).
- **Clock:** keep the server's clock synced with NTP. Set `CMS_TIMEZONE` to
  the contest's time zone, so the Admin Web Server shows the local equivalent
  of the ranking schedule (see "Windows" in
  [multi-contest.md](multi-contest.md)). The sidebar of the Admin Web Server
  also shows the current time in UTC and, when they differ, in the server's
  time zone and in the contest's.

Changes to `.env` apply when the containers are recreated (`./up.sh`).

### Set up the contests

1. Import the contests and their tasks. A contest imported from a backup
   with `./import.sh` arrives **inactive**, unless you chose to drop the
   database first (see `import.sh` in [docker-scripts.md](docker-scripts.md)).
2. Create the ranking groups, assign the contests, tick **Active** and set the
   hide and freeze windows: follow "Before an exam day" in
   [multi-contest.md](multi-contest.md). Create a hidden group **before**
   assigning a contest to it.
3. Create the teams and the contest's groups, then import the contestants:
   [importing-users.md](importing-users.md).
4. For every task that uses `depends_on`, follow "Before the contest" in
   [subtask-dependencies.md](subtask-dependencies.md).
5. If `CMS_TWO_PHASE_EVALUATION` is on, check the testcase codenames of every
   task and follow "Before the contest" in
   [Two-phase grading](two-phase-grading.md).
6. For every task, submit a correct solution and a wrong one from a test
   user, in each language the contestants will use, and check the scores.
7. For every OutputOnly task, count its outputs and keep them below about
   990: the per-file submission form stops working near 1000 outputs, and
   with more only the zip form works (see "OutputOnly" in
   {doc}`Task types`). Then submit every output at once from a test user,
   through the address the contestants use, so that the request goes through
   the reverse proxy's `max_size` (see "Request body limits" in
   [docker-deployment.md](docker-deployment.md)). Each output file costs the
   Contest Web Server about 13 ms during which it serves nobody else, so for a
   task with many outputs set a `min_submission_interval` on the task.

### Rehearse

- Run a rehearsal on a deployment with the same version and the same `.env`
  as the real one, with the same steps as on the day.
- Note the commit you deployed and the last known-good one, in case you have
  to roll back (see "Rollback" in [multi-contest.md](multi-contest.md)).
- Take a backup with `sudo ./backup.sh` once the contests are set up (see
  `backup.sh` in [Docker Scripts Guide](docker-scripts.md)). It has the
  ranking groups, their windows and staff passwords included. `./export.sh`
  is only for moving contests between deployments.

## Before the start

- Do not rebuild or update the deployment on the day. If you must deploy in
  an emergency:
  - Do not deploy while `./export.sh`, `./backup.sh` or another CLI tool
    is running.
  - The deploy can stop at `db-init` with
    `cmsSetupDB could not lock … within 5 s`: another session is using a
    table that the update changes, and that update changes nothing (an
    earlier update of the same run may already be applied). `./up.sh`
    does not print that message: `./logs.sh db-init` shows it. Meanwhile
    the contest is down: Compose stops the running `cms` container before
    `db-init` runs, and starts the new one only after `db-init` succeeds.
    If `db-init` fails, the new `cms` container does not start either, so
    CMS stays down until `./up.sh` succeeds: end the session that holds
    the lock, then run `./up.sh` again at once. Find that session with
    the holder query (see "When the database misbehaves"), end it, then
    run `./up.sh` again.
  - Start any SQL you apply by hand with `SET lock_timeout = '5s';`, so
    that it gives up instead of making the contest wait behind its lock.
- `./status.sh`: every container is `Up`, every program in the supervisor
  list is `RUNNING`, and the watchdog's line says that its last cycle was a
  few seconds ago.
- In the Admin Web Server, open **Overview**. The **Workers status** title
  reads "(N connected, M configured)": N must equal M.
- On **Ranking groups**, the **State** column says what you expect for each
  group, and its "next:" times match the contest (see "Checking a schedule"
  in [multi-contest.md](multi-contest.md)).
- Check the compile limit against your tasks. The submissions of the
  rehearsal, or of a previous contest, record the CPU seconds each
  compilation took. This query gives the median (`p50`), the 95th percentile
  (`p95`) and the maximum of those seconds for each task and language, over
  the active dataset of each task, the largest maximum first:

  ```sql
  SELECT t.name AS task, s.language, count(*) AS compilations,
         round(percentile_cont(0.5)  WITHIN GROUP (ORDER BY sr.compilation_time)::numeric, 2) AS p50,
         round(percentile_cont(0.95) WITHIN GROUP (ORDER BY sr.compilation_time)::numeric, 2) AS p95,
         round(max(sr.compilation_time)::numeric, 2) AS max
  FROM submission_results sr
  JOIN submissions s ON s.id = sr.submission_id
  JOIN tasks t ON t.id = s.task_id
  WHERE sr.dataset_id = t.active_dataset_id AND sr.compilation_time IS NOT NULL
  GROUP BY t.name, s.language
  ORDER BY max DESC;
  ```

  Run it as the holder query is run (see "When the database misbehaves"):
  in that command, put this query, written on one line (it has no quotes),
  between the double quotes after `-c`; or run it with `psql` on the
  database host. Compare the maximum of each row with
  `CMS_COMPILATION_MAX_TIME_S` (20 seconds when it is unset; see
  [docker-deployment.md](docker-deployment.md)). In the 2025 contest the
  C++20 maximum was 10.48 seconds against a limit of 10, and two
  compilations failed with a false "Compilation timed out". To change the
  limit, set `CMS_COMPILATION_MAX_TIME_S` in `.env` and run `./up.sh`,
  choosing no rebuild (1).
- Ask the contestants to **log in before the start**. Checking a password
  costs about 0.2 seconds of CPU, and each Contest Web Server process checks
  at most 4 at a time, so a whole room logging in at the start time makes
  everyone wait.

## During the contest

Keep the contest's **Overview** page open in the Admin Web Server. It
refreshes every 5 seconds:

- **Submissions status** (only on a contest's Overview): how many
  submissions of this contest are scored, failed to compile, or are still
  compiling, evaluating or scoring. **Scored** is always shown; any other
  row is hidden while its count is 0. Watch for **Cannot compile (please
  check)** and **Cannot evaluate (please check)**.
- **Queue status:** the jobs waiting for a worker, for all the contests
  together; a submission can have more than one. Jobs already running on a
  worker are not listed here.
- **Workers status:** whether each worker is connected and what it is
  doing.
- **Logs:** the last 100 warnings and errors of the CMS services (not of the
  Ranking Web Server).

For the full logs, `./logs.sh -f cms` follows the CMS services (press
`Ctrl+C` to stop) and `./logs.sh --tail 2000 cms` shows the last 2000
lines.

The contest's **Activity** page, in its menu, lists under **Simultaneous
activity** the accounts in use from two browsers at the same time (see
[Participant activity](participant-activity.md)).

Common problems:

- **A scoreboard does not match the database:** press **Regenerate** on its
  ranking group (see "When a scoreboard is wrong" in
  [multi-contest.md](multi-contest.md)). Not while database errors
  continue: a failed regenerate can leave the scoreboard empty.
- **A worker shows Connected: No:** its job goes back to the queue by itself
  within seconds. Restart that worker, numbered from 0 as in the **Shard**
  column:

      docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
          exec cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf \
          restart cmsworker<shard>

- **A worker was disabled:** a worker that does not finish its jobs in
  time is disabled and its jobs go back to the queue (`./logs.sh cms` says
  "Disabling and shutting down worker" and "put again in the queue because
  of worker timeout"; the Overview **Logs** table shows the first of them
  but not the second). The time allowed grows with the jobs: at least 10
  minutes, about 15 minutes for a full batch of 25 evaluations with a 1 s
  time limit and about 30 minutes with a 10 s one. The worker is told to
  quit; when it has restarted and reconnects it is enabled again by itself
  (the log says "enabling it again"). Press **Enable** on its row of
  **Workers status** only for a worker that stays disabled, for example one
  disabled with the **Disable** button; a worker that stays disabled after
  a timeout is stuck, so restart it first (see above).

- **`QueuePool limit … reached` in the logs:** the Contest Web Server ran
  out of database connections (see [Troubleshooting](Troubleshooting.rst)).
- **Extending a contest:** the hide and freeze times of a ranking group are
  absolute. Move them by hand to match the new end.

## When the database misbehaves

CMS does not wait forever for PostgreSQL. A new connection that gets no
answer fails after 10 seconds with `timeout expired`. A call on a
connection that died fails within about a minute, with
`server closed the connection unexpectedly`,
`could not receive data from server` or `Connection timed out`. Pages then
show an error instead of hanging, and every service reconnects by itself
once PostgreSQL answers again.

The driver's error text is in `./logs.sh --tail 2000 cms`. The **Logs**
table on **Overview** shows only the message of each line, not the error
text that follows it.

### PostgreSQL does not answer

1. Check from inside the `cms` container, with the URL of `.env` without
   its `+psycopg2` (the command strips it, as `contest.sh` does):

   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
       exec -T cms bash -c 'timeout 5 pg_isready -t 3 -d "${CMS_DB_URL/+psycopg2/}"'
   ```

   Without `-d`, `pg_isready` checks the container's own socket instead.
2. Then check PostgreSQL itself on its host: its status, its log and the
   free disk space.
3. Do not restart CMS while PostgreSQL does not answer, and do not edit
   `.env` because a log line says
   `Is the connection string in cms.toml correct?`.
4. Once PostgreSQL answers, CMS recovers by itself: pages within a
   minute, grading within about 2 minutes and scores within about 6
   minutes. Then run `./status.sh`: every program must be `RUNNING`. If
   one is not, restart it with the `supervisorctl` command of "A worker
   shows Connected: No" (above), with the program's name from
   `./status.sh` instead of `cmsworker<shard>`.
5. If grading or scoring has not resumed 3 minutes after PostgreSQL
   answers, run the holder query (below) and end every session whose
   `open_for` is longer than the time since the outage began and whose
   `state` is `idle in transaction`, or `active` with `wait_event_type`
   `Client` (it waits for a client that is gone). After a network cut,
   PostgreSQL can keep such a transaction, and its locks, for about 2
   hours.
6. Do not press **Regenerate** while database errors continue: a failed
   regenerate can leave a scoreboard empty until it is pressed again.

### PostgreSQL is stalled

Pages and grading freeze, new connections fail with `timeout expired`,
and `pg_isready` reports `no response`. PostgreSQL itself is stuck,
usually on its disk. No CMS timeout can cut this, and restarting CMS does
not help: check the disk and the PostgreSQL log on its host.

### The holder query

It lists the open transactions, the oldest first, with the program that
holds each one (`application_name`, for example `cmsContestWebServer 2`)
and the sessions that wait for it (`blocked_by`). Run it from the `cms`
container:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms bash -c 'psql "${CMS_DB_URL/+psycopg2/}" -c "SELECT pid, application_name, state, wait_event_type, now() - xact_start AS open_for, pg_blocking_pids(pid) AS blocked_by, left(query, 60) AS query FROM pg_stat_activity WHERE datname = current_database() AND xact_start IS NOT NULL ORDER BY xact_start;"'
```

Or run the same `SELECT` with `psql` on the database host.

To end a session, run `SELECT pg_terminate_backend(<pid>);` the same way.
The `cms` role can end its own sessions; ending another role's session
needs a superuser.

### Switching the timeouts off

If these errors show up under normal load while PostgreSQL is healthy,
add this suffix to `CMS_DB_URL` in `.env`, with `&` instead of its `?` if
the URL already has a `?`:

```text
?connect_timeout=0&keepalives_idle=7200&keepalives_interval=75&keepalives_count=9&tcp_user_timeout=0
```

Then run `./up.sh` and choose no rebuild (1): it recreates the
containers, so every service restarts. CMS then waits for PostgreSQL
without a short limit again, as before. The suffix does not switch off the
5-second lock limit of `cmsSetupDB` (see "Before the start").

### Checking the settings after a deploy

A new connection must get these settings. In the `cms` container:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms python3 -c 'import os, socket; from cms.db import custom_psycopg2_connection; c = custom_psycopg2_connection(); s = socket.socket(fileno=os.dup(c.fileno())); print("TCP_USER_TIMEOUT", s.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT)); s.close(); k = c.cursor(); k.execute("SHOW application_name"); print("application_name", k.fetchone()[0])'
```

It must print `TCP_USER_TIMEOUT 60000` (or the value that `CMS_DB_URL`
sets) and a non-empty `application_name`: here `-c`, the name of a
`python3 -c` program. Then
`./logs.sh --tail 2000 cms | grep -E 'timeout expired|closed the connection|Lost the database|Connection timed out|could not receive data from server'`
must print nothing.

## When the watchdog alerts

`cmsWatchdog` checks every CMS program every 15 seconds (see "The watchdog"
in [docker-deployment.md](docker-deployment.md)). In `./logs.sh cms` its lines
read `<UTC time> <LEVEL> Watchdog: …`, or `<UTC time> <LEVEL> Resolved: …`
when a problem is over; its warnings and errors also appear in the **Logs**
table on **Overview**, from `Watchdog,0`. `./status.sh` shows its state. In
`observe` mode it restarts nothing: its `WOULD restart` lines say what
`enforce` would have done.

### What its lines mean

| Line | What it means | What to do |
|------|---------------|------------|
| `started in … mode …` | The watchdog started (also each time the container starts). It acts on nothing in its first 120 seconds. | Nothing. |
| `early warning: …` | A program missed 3 of its last 6 checks, or one of its sections is past half its limit. | Watch it; read `./logs.sh cms`. The line says what the watchdog does at the limit, in this mode, and which hold applies now. |
| `restarted … (pid A→B …) after …` | It restarted a hung program: TERM, then KILL after 10 seconds if needed. The end of the line says where its stacks are, or why there are none. | Read the evidence file it names, and the stacks in `./logs.sh cms`. For a Contest Web Server, see "When a Contest Web Server restarts". |
| `WOULD restart … after …` | In `observe` mode, or for a class that is not in `CMS_WATCHDOG_RESTART`, it would have restarted the program. | Decide by hand, after `cmsWatchdog --snapshot` (see "The evidence"). |
| `… answers again N s after the restart` | The restart helped: the program passes its checks again. | Nothing. |
| `… is still failing 120 s after the restart` | The restart did not help. | Read the evidence and the logs; restart again by hand only once the cause is known. |
| `sent TERM … but no new pid was RUNNING within 30 s` | It signalled the program, but supervisord did not show it `RUNNING` again within 30 seconds. | Check `./status.sh`. If the program is `FATAL`, read `./logs.sh cms`, fix the cause, then start it with `supervisorctl`. |
| `did not restart …; nothing was signalled` | Between its check and the restart, the program exited or got another pid, so it did nothing. | Nothing, if `./status.sh` shows it `RUNNING`. |
| `did not restart … after …: …, so no TERM or KILL was sent` | The program exited by itself before the watchdog's TERM (perhaps after its evidence and USR1), so it sent no TERM or KILL. | Check `./status.sh`. |
| `could not restart … (pid N) after …` | The restart failed; the line says why, and whether TERM was sent. | Check `./status.sh`, and restart it by hand if needed, after `cmsWatchdog --snapshot`. |
| `did not restart …: its ledger could not be written …` | It could not record the restart in its ledger, so it did nothing: an unrecorded restart would escape the budget. | Check the free space of the run directory, which holds the ledger: in the `cms` container it is `/home/cmsuser/cms/run`, a tmpfs held in memory, not the CMS log volume, so check `df -h /home/cmsuser/cms/run` there and the server's free memory. Until the ledger can be written, it restarts nothing. |
| `… fails R… (…) but is held by H…`, `N Workers trip R4 together (…) and are held by H…` or `N programs fail R5 together (…) and are held by H…` | It would restart the program (or start it, for R5), but a hold stops it. | Follow the row of that hold in "The holds". |
| `the database gate is red (…)` | PostgreSQL does not answer, or has no free connection; nothing is restarted meanwhile. | Follow "When the database misbehaves". |
| `FATAL, and in the last … minutes the watchdog saw no database outage` or `N programs are FATAL (…), and in the last … minutes the watchdog saw no database outage` | A program stopped for a reason other than a database outage. | Read `./logs.sh cms`, fix the cause, then start it with `supervisorctl`. |
| `started …, left FATAL by a database outage` | After an outage, it started the programs the outage had left `FATAL`. | Check `./status.sh`. |
| `WOULD start …, left FATAL by a database outage` | In `observe` mode, or for a class that is not in `CMS_WATCHDOG_RESTART`, it would have started them. | Check `./status.sh`, and start them with `supervisorctl`. |
| `could not start all of …` or `did not start …: its ledger could not be written …` | supervisord did not start some of them, or the watchdog could not record the batch in its ledger and started nothing. | Read `./logs.sh cms`, then start them with `supervisorctl`. |
| `re-evaluation still running for N min` | A re-evaluation holds the Evaluation Service's lock; it is never restarted for that. | Nothing, unless new submissions must be graded first. |
| `ranking … does not answer` | A ranking does not answer; the watchdog cannot restart it. | Check the ranking container with `./status.sh` and `./logs.sh ranking`. |
| `pid N answers on the RPC port of …` | Another process answers on the program's RPC port, not the one supervisord started. The watchdog does nothing about it. | Check `./status.sh` and `ps` in the `cms` container, and end the stray process by hand. |
| `Resolved: …` | A problem it alerted about is over: the program passes its checks again, the database answers again, or the ranking answers again. | Nothing. |
| `the evidence of … could not be written`, or `its journal …`, `its status file …` or `its status history … could not be written` | A file of the watchdog could not be written (a full disk, for example); its restarts go on. | Check the free space where the file it names is. The evidence and the journal (`actions.jsonl`) are in `/home/cmsuser/cms/log/watchdog`, on the CMS log volume: check `df -h /home/cmsuser/cms/log` in the `cms` container. The status file is in `/home/cmsuser/cms/run`, a tmpfs held in memory: check `df -h /home/cmsuser/cms/run` there and the server's free memory. The status history exists only in the load-test harness. |
| `its ledger … could not be written` | Its ledger could not be written at the end of a cycle: a restart stops at its booking until it can be. | Check the free space of the run directory, which holds the ledger: in the `cms` container it is `/home/cmsuser/cms/run`, a tmpfs held in memory, not the CMS log volume, so check `df -h /home/cmsuser/cms/run` there and the server's free memory. Until the ledger can be written, it restarts nothing. |
| `its ledger … did not parse (…)` | At its start, its ledger could not be read: every budget counts as used for 60 minutes. | Nothing; restart by hand if needed, after `cmsWatchdog --snapshot`. |
| `the restart of …`, `the start of …` (or `the would-restart of …`, `the would-start of …`) followed by `failed unexpectedly: …` or `was interrupted: …` | An unexpected error stopped one of its actions, or the watchdog was stopped while it ran one. | Check `./status.sh`, and keep the lines around it for the developers. |
| `its cycle failed unexpectedly (…); it exits, and supervisord starts it again` | An unexpected error in the watchdog itself; its traceback is in `./logs.sh cms`. | Check `./status.sh`: if `cmswatchdog` is `FATAL`, supervisord gave up starting it, and nothing watches the programs. Keep the lines around it for the developers. |
| `it has started N times in the last hour, so it is crash-looping (…)` | The watchdog started more than 5 times within an hour: each run ends in an error or at its self-guard, and supervisord, which never gives up on it, starts it again at once. Meanwhile nothing watches the programs. The line appears when the count reaches 6; a loop of one start every 10 to 12 minutes repeats it at every start. The count starts again when the container restarts. | Search `./logs.sh cms` for `cycle failed unexpectedly` (an error) or for a line that starts with `Timeout (` (the self-guard), and read the traceback. To stop the loop, stop `cmswatchdog` or set `CMS_WATCHDOG=off` (see "Turning it off"). Keep the traceback for the developers. |
| `paused for N s` | The watchdog itself was paused (the host, the VM or the container). | Nothing, if it happens once. |
| `… is gone: no process runs it any more …` | Only in the load-test harness, which reads `/proc` instead of supervisord. | Nothing. |
| `hourly near-miss summary` | How close each check came to its limit in the last hour. | Nothing; it is the record for switching `enforce` on. |

### The holds

A hold stops a restart and leaves an alert. The alert names the first hold
that applies (`held by H…`) and lists the others. While the hold lasts, the line
repeats every 5 minutes with the hold that applies then; the first time H7 or H3
applies, it goes out at once.

| Hold | It applies when | What to do |
|------|-----------------|------------|
| H1 | supervisord did not answer. | Run `./status.sh`. |
| H2 | The database does not answer, or has answered for less than 120 seconds. | Follow "When the database misbehaves". |
| H3 | A CMS session has waited more than 30 seconds for a database lock. The alert names up to 3 waiting sessions and up to 3 blockers of each: its pid, its program, its state and how long its transaction has been open. | See "A lock blocks CMS". Ending a blocker is your decision. |
| H4 | Two or more programs of the same class fail together (two Contest Web Servers, for example). | Find what they share (the database, the disk, the load). Restart them by hand one at a time, and only once you know the cause. |
| H5 | More than a third of the programs it probes fail a check at once (the Workers are not counted). | Check the load of the server and of the database. |
| H6 | The program, or the watchdog itself, started less than 120 seconds ago, or the watchdog acted on it less than 120 seconds ago. | If the program or the watchdog started: wait. If the watchdog acted on it (or would have): it judges the program again 120 seconds after that; in `observe` mode, or for a class that is not in `CMS_WATCHDOG_RESTART`, it restarts nothing, so decide by hand, after `cmsWatchdog --snapshot`. |
| H7 | Its budget is used: 2 restarts of a program, or 3 of its pool, per hour; for a `FATAL` program, 2 starts per hour; or its ledger did not parse, and every budget counts as used for 60 minutes. The alert says until when (`will not restart … before HH:MM UTC`, or `will not start … before HH:MM UTC`). | A running program: restart it by hand if needed, after `cmsWatchdog --snapshot`. A `FATAL` program: read `./logs.sh cms`, then start it by hand with `supervisorctl` if needed. |
| H8 | Another restart is still running. | Nothing. |

### A lock blocks CMS

An H3 alert names the session that holds the lock, for example
`pid 900 ('psql', idle in transaction, transaction open 300 s)`. If it is a
session you know (an operator's `psql`, a long export), let it finish or end
it. Find it with the holder query of "When the database misbehaves", and end
it with `SELECT pg_terminate_backend(<pid>);` the same way.

After a restart, a blocker marked `orphan of pid N` is the database session of
the process the watchdog killed (pid N): its connection is gone, but
PostgreSQL may keep its transaction and its locks. End it with
`pg_terminate_backend` as above. End only a blocker marked that way: an
unmarked blocker with the same program name may be the new process's own
session.

### The evidence

Before every restart and every `WOULD restart`, the watchdog writes a file in
the `watchdog/` directory of the CMS log volume (`log/watchdog/` in its
alerts): the decision, the program's last 6 checks, the last `health()`
answer it received from the program (with the stack of a stuck section), the
database sessions and the state of the process. It also makes the program
write the stack of each of its threads to `./logs.sh cms` (lines that start
with `Thread 0x` and `Current thread 0x`), unless the program is stopped.
`actions.jsonl`, in the same directory, lists every action with its time. To
list them:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms ls -l /home/cmsuser/cms/log/watchdog
```

These files are sensitive: they name the database sessions and show their
statements (the quoted values in them are left out; numbers, comments and
names stay), so look through one before you post it anywhere public.

Before you restart a program by hand, keep the same evidence with:

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
    exec -T cms cmsWatchdog --snapshot <program>
```

`<program>` is the name that `./status.sh` shows, for example
`cmscontestwebserver2`. It restarts nothing, and it prints the file it wrote.

### When a Contest Web Server restarts

- With the Caddy profile of "Health checks in the reverse proxy" in
  [docker-deployment.md](docker-deployment.md), its users move to another
  process within about 35 seconds; without it, they get errors for up to 30
  seconds after the restart.
- A submission cut off by the restart may have been saved: tell the
  contestants to check their list of submissions before they send it again.
- Up to 60 seconds of participant activity of that process are lost. The
  restart times, for the review of simultaneous activity, are in
  `actions.jsonl`.
- If every process answers 503 with `no upstreams`, it heals by itself within
  about 30 seconds: restart nothing.

### Turning it off

- **Until the container restarts**, with no downtime:

  ```bash
  docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
      exec cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf stop cmswatchdog
  ```

- **For good:** set `CMS_WATCHDOG=off` in `.env`, then run `./up.sh`; it
  recreates the containers, so every service restarts.

### Switching enforce on

Only between contests (never on a contest day, nor in the 3 days before one),
and only for the classes that passed all of these in `observe` mode:

- every drill of the watchdog's validation passed on the deployed version;
- at least one session with real users (a contest, or a rehearsal of 2 hours
  or more) ran in `observe` mode;
- every `WOULD restart` line is explained, and no H4 or H5 alert appeared
  outside known database events;
- the hourly summaries stayed under half of each limit: at most 2 misses in 6
  checks, no Evaluation Service section older than 90 seconds with no
  statement for 30 seconds (re-evaluations aside), no scoring longer than 90
  seconds, and no ranking send idle for 90 seconds;
- the nightly 05:00 restart was seen at least 7 times;
- you ran the game day (below).

Then, in one edit of `.env`, set `CMS_WATCHDOG=enforce` and set
`CMS_WATCHDOG_RESTART` to only those classes (for example
`CMS_WATCHDOG_RESTART=web`), run `./up.sh`, and run the game day again. Add
`workers` later, once `observe` mode has shown no false Worker alert.

A value of `CMS_WATCHDOG` or `CMS_WATCHDOG_RESTART` that is not valid (a typo)
stops the `cms` container from starting: `./up.sh` fails its wait within about
90 seconds, and `./logs.sh cms` says which variable is wrong.

### The game day

In a quiet window and never on a contest day: once in `observe` mode, and once
right after switching `enforce` on.

1. Stop a Contest Web Server for 120 seconds, then let it go on (here process
   3; use one that exists):

   ```bash
   docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
       exec -T cms bash -c 'pid=$(supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf pid cmscontestwebserver3); kill -STOP "$pid"; sleep 120; kill -CONT "$pid"'
   ```

2. Do the same with the Scoring Service (`cmsscoringservice`) for 240
   seconds.
3. Check the reverse proxy's log, the **Logs** table on **Overview**,
   `./logs.sh cms` and the evidence directory: an early warning, then
   `WOULD restart` (`restarted` in `enforce` mode), and no line about the
   other programs.

## At the end

1. **Last-second submissions.** There is no grace period. A submission
   counts by the time its request reached the reverse proxy if the request
   time header is on (see "Submissions at the contest stop" in
   [docker-deployment.md](docker-deployment.md)), and by the time the server
   processes it if not; never by the time the contestant pressed the button.
   Without the header, a busy server can judge a submission sent just before
   the end as late: set the contest stop about one minute after the end you
   announce. A late submission is not accepted (unless the contest allows
   unofficial submissions before the analysis mode): the form sends the
   contestant back to the contest page with the error "Submission not
   accepted" / "The contest has already ended." ("Envío no aceptado" / "La
   competencia ya finalizó." in Spanish). Tell the contestants beforehand
   not to leave their submissions for the last seconds.

   A submission refused because another request from the same account was
   being processed at the same moment ("Another request from your
   account…", see "When something goes wrong") can be sent again only
   before the stop: after it, that refusal is final.
2. **Wait for the evaluation to finish.** The submissions made before the
   end are still being evaluated after it. In the load test the queue took 1
   to 5 minutes to drain with 8 workers. It is done when, on each contest's
   **Overview**, **Queue status** reads "Queue empty." and **Submissions
   status** has no **Compiling...**, **Evaluating...** or **Scoring...**
   row left. **Scored** and **Compilation failed** are final. Deal with any
   **Cannot compile** or **Cannot evaluate** row first.
3. **Only then publish the final ranking:** press **Descongelar ahora** or
   **Mostrar ahora** on the ranking group (see 'The "now" buttons' in
   [multi-contest.md](multi-contest.md)). If the unfreeze or show time is
   scheduled, put it late enough after the end for the queue to drain.
4. Check the published scoreboard against the scores in the Admin Web
   Server. If they differ, press **Regenerate**.
5. Untick **Active** on each contest to close it to the contestants. Its
   scoreboard stays published.

## After the contest

- Take a backup with `sudo ./backup.sh`, copy it off the server with the
  `rsync -a` line that it prints (it needs `sudo rsync` without a password on
  the server), and check the copy with
  `./verify-backup.sh` (see `backup.sh` and `verify-backup.sh` in
  [Docker Scripts Guide](docker-scripts.md)).
- A restore means downtime: CMS is stopped while the databases are swapped.
  The lead decides it, and follows "Restoring a backup" in
  [Docker Scripts Guide](docker-scripts.md).
- Download the activity CSV of each contest before you remove
  participations or restore with `./import.sh`: an `./export.sh` backup does
  not include it (see "After the contest" in
  [Participant activity](participant-activity.md)).
- An `./export.sh` backup does not contain the ranking groups either. After
  restoring it with `./import.sh`, even with `-d`, create the ranking groups
  again and assign the contests (see `import.sh` in
  [Docker Scripts Guide](docker-scripts.md)).
- If a ranking group was renamed or deleted, remove its old scoreboard (see
  "Removing an old scoreboard" in [multi-contest.md](multi-contest.md)).

## When something goes wrong

| Symptom | What to do | Details |
|---------|------------|---------|
| A scoreboard is missing scores or shows a contest it should not | **Regenerate** on its ranking group, unless database errors continue | [multi-contest.md](multi-contest.md) |
| A scoreboard stopped updating and ProxyService logs "rejected the visibility" | Follow "Failure mode" | [multi-contest.md](multi-contest.md) |
| A page does not load | `./status.sh`, then `./logs.sh` | [docker-scripts.md](docker-scripts.md) |
| A contestant gets an "Error 400" page when submitting, and the Contest Web Server log has a WARNING for a 400 on a `…/submit` path with `Invalid body` in it | For an OutputOnly task, ask them to use the zip form; also check the `max_size` of the reverse proxy. The page is generic, with a paragraph about tampering: tell the proctors it does not mean disqualification | "OutputOnly" in {doc}`Task types`; "Request body limits" in [docker-deployment.md](docker-deployment.md) |
| A contestant gets a 413 or a connection reset when submitting, with no CMS page | The submission is over the `max_size` of the reverse proxy: raise it | "Request body limits" in [docker-deployment.md](docker-deployment.md) |
| A contestant reports "Another request from your account was being processed at the same moment…" ("Otro pedido de su cuenta se estaba procesando en el mismo momento…" in Spanish) | Two devices, or a script, sent a request from that account at the same instant, and only one was accepted: they send it again. To confirm, check **Simultaneous activity** on the contest's **Activity** page and `./logs.sh --tail all --since 30m cms \| grep "concurrent request"` | "During the contest" and "Last-second submissions" in "At the end" (above) |
| A submission stays in **Cannot compile** or **Cannot evaluate** | Read the **Logs** table on **Overview** and the submission's page | [Troubleshooting](Troubleshooting.rst) |
| In a task with `depends_on`, dependent subtasks are not zeroed as expected, or submissions finish late | Search the Evaluation Service log for "cannot be used" and "gates are holding each other" | [subtask-dependencies.md](subtask-dependencies.md) |
| A contest is not listed for the contestants | Tick **Active** on it | [multi-contest.md](multi-contest.md) |
| ProxyService keeps logging "Could not send … trying again in … seconds." | Check the ranking container with `./status.sh` and `./logs.sh ranking`; the data goes through by itself once it answers | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "refused … they will be sent again after the contest data" | Nothing: the scores come back within about 6 minutes. If it repeats for the same ids, fix the cause (reason in `./logs.sh ranking`) and the next sweep sends them again by itself, or use **Regenerate** on the group to send them at once (it briefly empties the scoreboard) | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "refused … the contest data of the group will be sent again" | Nothing at first: the next sweep, within about 6 minutes, sends the contest data of the group again. If it repeats for the same ids, fix the data (reason in `./logs.sh ranking`) and the next sweep sends it again by itself, or use **Regenerate** on the group to send it at once (it briefly empties the scoreboard) | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "rejected … It will not be sent again" | The "Status … while …" line before it shows a code other than 400: fix the cause (usually the ranking URL or credentials of ProxyService), then **Regenerate** on the group | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "cannot be encoded for ranking" | Fix the data, then **Regenerate** on the group | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "Contest … still cannot be sent to the rankings" | Fix what the error names; the contest is sent again within about 6 minutes | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "Reinitializing the rankings failed." | Fix the cause, then **Regenerate** on the groups it lists, or on those of what you changed | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| ProxyService logs "accepted the visibility of group" | **Regenerate** on that group | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| The logs say "EvaluationService cannot notify ScoringService,0 (…); nothing is lost, its sweeper will score these submissions." (then "still cannot notify", at most once a minute) | Run `./status.sh` and restart `cmsscoringservice` if it is not `RUNNING`, as in "A worker shows Connected: No". The Scoring Service's sweeper scores the submissions when it starts, otherwise within about 6 minutes (347 s). Do not press **Regenerate**: it scores nothing. Before publishing the final ranking, wait until **Submissions status** has no **Scoring...** row left | "During the contest" and "At the end" (above) |
| The logs say "ScoringService cannot notify ProxyService,0 (…); nothing is lost, its sweeper will send these scores to the rankings." (then "still cannot notify") | Run `./status.sh` and restart `cmsproxyservice` if it is not `RUNNING`, as in "A worker shows Connected: No"; for what ProxyService itself logs, follow the ProxyService rows above. ProxyService's sweeper sends the scores to the rankings when it starts, otherwise within about 6 minutes | "ProxyService warnings that need an operator" in [Running several contests at once](multi-contest.md) |
| The logs say "No pending request with id … found." | An answer came for a call that was never sent or that was already answered, which should not happen, except after a stall of more than 4096 abandoned calls: keep the lines around it for the developers. It is not a late answer: those are logged at DEBUG as "Late answer to …, the caller gave up.", and many of them at once only mean that a stuck service answered after a stall (the caller logged its timeout when it gave up) | "During the contest" (above) |
| Pages show an error after about 10 seconds, and the logs say `timeout expired` | A new database connection got no answer: follow "PostgreSQL does not answer" | "When the database misbehaves" (above) |
| The logs say `server closed the connection unexpectedly`, `could not receive data from server`, `Connection timed out`, `Lost the database connection before the session's rollback` or `Lost the database connection while writing worker results` | Once, from one service: nothing, it reconnects by itself. From several services at once: follow "PostgreSQL does not answer" | "When the database misbehaves" (above) |
| A deploy stops at `db-init`, and `./logs.sh db-init` shows `cmsSetupDB could not lock … within 5 s` | Find the session with the holder query, end it, then run `./up.sh` again | "When the database misbehaves" (above) |
| The logs say `Finalized result … (a write was cut)` | Nothing: the Evaluation Service completed a result whose outcome write was cut. If it repeats for many results, follow "PostgreSQL does not answer" | "When the database misbehaves" (above) |
| `Watchdog: … held by H…` in the logs or in the **Logs** table | Follow the row of that hold in "The holds" | "When the watchdog alerts" (above) |
| `Watchdog: restarted …` or `Watchdog: WOULD restart …` | Read the evidence; tell the contestants of a restarted Contest Web Server to check their submissions | "When the watchdog alerts" (above) |
| `./status.sh` shows `no status file`, or a watchdog line whose last cycle is minutes old | The watchdog is not running: check its row (`cmswatchdog`) in `./status.sh` and read `./logs.sh cms` | "When the watchdog alerts" (above) |
