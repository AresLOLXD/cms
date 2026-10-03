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
  with 4, none (see "At the end" for why).
- **Ports:** process *i* listens on `CMS_CWS_HTTP_PORT` + *i*. Nothing checks
  that this range stays clear of `CMS_AWS_HTTP_PORT` and `CMS_RWS_HTTP_PORT`,
  and with the defaults (8888, 8889, 8890) a second process lands on the
  Admin port. Move the Admin and Ranking ports above the range, and balance
  the processes with `ip_hash` in the reverse proxy. See "Scaling Contest Web
  Server" in [docker-scripts.md](docker-scripts.md).
- **Clock:** keep the server's clock synced with NTP. Set `CMS_TIMEZONE` to
  the contest's time zone, so the Admin Web Server shows the local equivalent
  of the ranking schedule (see "Windows" in
  [multi-contest.md](multi-contest.md)).

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
5. For every task, submit a correct solution and a wrong one from a test
   user, in each language the contestants will use, and check the scores.

### Rehearse

- Run a rehearsal on a deployment with the same version and the same `.env`
  as the real one, with the same steps as on the day.
- Note the commit you deployed and the last known-good one, in case you have
  to roll back (see "Rollback" in [multi-contest.md](multi-contest.md)).
- Take a backup with `./export.sh` once the contests are set up.

## Before the start

- Do not rebuild or update the deployment on the day.
- `./status.sh`: every container is `Up`, and every program in the
  supervisor list is `RUNNING`.
- In the Admin Web Server, open **Overview**. The **Workers status** title
  reads "(N connected, M configured)": N must equal M.
- On **Ranking groups**, the **State** column says what you expect for each
  group, and its "next:" times match the contest (see "Checking a schedule"
  in [multi-contest.md](multi-contest.md)).
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

Common problems:

- **A scoreboard does not match the database:** press **Regenerate** on its
  ranking group (see "When a scoreboard is wrong" in
  [multi-contest.md](multi-contest.md)).
- **A worker shows Connected: No:** its job goes back to the queue by itself
  within seconds. Restart that worker, numbered from 0 as in the **Shard**
  column:

      docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
          exec cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf \
          restart cmsworker<shard>

- **A worker was disabled:** a worker that stays busy with one job for more
  than 10 minutes is disabled and its job goes back to the queue (`./logs.sh
  cms` says "put again in the queue because of worker timeout"; this message
  is not in the Overview **Logs** table). It gets no more jobs, even after a
  restart, until someone presses **Enable** on its row of **Workers
  status**.

- **`QueuePool limit … reached` in the logs:** the Contest Web Server ran
  out of database connections (see [Troubleshooting](Troubleshooting.rst)).
- **Extending a contest:** the hide and freeze times of a ranking group are
  absolute. Move them by hand to match the new end.

## At the end

1. **Last-second submissions.** There is no grace period. A submission
   counts by the time the server processes it, not the time the contestant
   pressed the button: a busy server can judge a submission sent just before
   the end as late. A late submission is not accepted (unless the contest
   allows unofficial submissions before the analysis mode), and the form
   only sends the contestant back to the contest page, which says "The
   contest has already ended." ("La competencia ya finalizó." in Spanish).
   Tell the contestants beforehand not to leave their submissions for the
   last seconds.
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

- Take a backup with `./export.sh` and copy the file from `dumps/` off the
  server.
- If a ranking group was renamed or deleted, remove its old scoreboard (see
  "Removing an old scoreboard" in [multi-contest.md](multi-contest.md)).

## When something goes wrong

| Symptom | What to do | Details |
|---------|------------|---------|
| A scoreboard is missing scores or shows a contest it should not | **Regenerate** on its ranking group | [multi-contest.md](multi-contest.md) |
| A scoreboard stopped updating and ProxyService logs "rejected the visibility" | Follow "Failure mode" | [multi-contest.md](multi-contest.md) |
| A page does not load | `./status.sh`, then `./logs.sh` | [docker-scripts.md](docker-scripts.md) |
| A submission stays in **Cannot compile** or **Cannot evaluate** | Read the **Logs** table on **Overview** and the submission's page | [Troubleshooting](Troubleshooting.rst) |
| In a task with `depends_on`, dependent subtasks are not zeroed as expected, or submissions finish late | Search the Evaluation Service log for "cannot be used" and "gates are holding each other" | [subtask-dependencies.md](subtask-dependencies.md) |
| A contest is not listed for the contestants | Tick **Active** on it | [multi-contest.md](multi-contest.md) |
