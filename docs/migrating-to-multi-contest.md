# Migrating from one deployment per contest to a single multi-contest deployment

This guide is for setups that ran parallel exams with **two (or more) copies**
of this Docker deployment on the same machine, each with its own database,
ports and scoreboard (for example scoreboards on 8890 and 7890 behind
different domains). After migrating, one deployment serves every contest and
each olympiad keeps its own scoreboard. See
[multi-contest.md](multi-contest.md) for day-to-day use.

Below, **stack A** is the deployment you keep and **stack B** the one you
retire. Run each command from that stack's directory.

## 1. Back up everything

For each stack, while it is running:

    # Database (local Docker database, --profile localdb)
    source .env
    docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb \
      -p "$CMS_PROJECT_NAME" exec -T db \
      pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup-$(date +%F).sql

    # Contest data, as a CMS dump
    ./export.sh

If you use an external database, run `pg_dump` against the URL in
`CMS_DB_URL` instead. Keep these files until the first exam on the new setup
has finished.

## 2. Update stack A

    git pull
    ./restart.sh     # answer "yes" to rebuilding the image

The database schema is updated automatically when the stack starts (the
`db-init` container adds the `ranking_groups` table and the new contest
columns). Existing contests start as **inactive** and without a ranking group;
nothing changes for contestants until you switch to `CMS_CONTEST_ID=ALL`.
If stack A **already** runs with `CMS_CONTEST_ID=ALL`, its contest list is
empty right after the update: tick **Active** on its contests in the Admin
Web Server straight away.
Check `./logs.sh` for errors before continuing.

## 3. Bring stack B's contests into stack A

1. **Check for name collisions first.** Usernames, team codes and contest
   names must be unique across both databases. List the ones present in both:

       # in each stack's directory (use its own .env)
       source .env
       docker compose -f docker/docker-compose.prod.yml --env-file .env --profile localdb \
         -p "$CMS_PROJECT_NAME" exec -T db psql -U "$POSTGRES_USER" \
         "$POSTGRES_DB" -Atc "SELECT username FROM users ORDER BY 1" > users.txt

       # then, with both files side by side
       comm -12 stackA/users.txt stackB/users.txt

   Repeat with `SELECT code FROM teams` and `SELECT name FROM contests`. If
   the same person has an account in both stacks, import only one copy: when
   exporting in stack B, leave users out, and add those participations in
   stack A afterwards. Rename anything else that collides in stack B (from its
   Admin Web Server) before exporting.
2. In stack B: `./export.sh` and pick the contests to move.
3. Copy the new `.zip` from stack B's `dumps/` into stack A's `dumps/`.
4. In stack A: `./import.sh`, pick that file, and **do not** wipe the
   database.

Dumps made before this update import fine: their contests arrive inactive and
without a ranking group.

## 4. Update stack A's `.env`

- `CMS_CONTEST_ID=ALL`
- `CMS_WORKER_COUNT`: at least the sum of what both stacks used.
- Nothing else needs to change: the scoreboard keeps `CMS_RWS_HTTP_PORT`
  (for example 8890). Stack B's ports (for example 7890) are no longer
  used.

Apply it with `./restart.sh`.

## 5. Configure the contests in the Admin Web Server

1. **Ranking groups:** create one per olympiad (for example `olim` and
   `omips`).
2. **Each contest:** tick **Active** where needed and choose its ranking
   group.
3. **Ranking groups → Root ranking → Regenerate:** clears the old
   single-contest scoreboard at `/`.

## 6. Update the reverse proxy (on the server)

Each scoreboard domain now points at the same port plus its group:

    # before
    server_name ranking.omips.example;  proxy_pass http://127.0.0.1:7890/;
    # after
    server_name ranking.omips.example;  proxy_pass http://127.0.0.1:8890/omips/;

Do the same for the other olympiad (`…:8890/olim/`), keep
`proxy_buffering off;`, and point every contestant domain at stack A's
Contest Web Server. Reload the proxy.

## 7. Verify, then retire stack B

- Each scoreboard domain shows only its own contests and updates live when a
  test submission is scored.
- The contest list shows exactly the active contests.
- Then stop stack B: `./down.sh` in its directory. Keep its volumes and the
  backups until you are confident.

### Rolling back

1. In stack A: set `CMS_CONTEST_ID` back to the previous contest id and
   `./restart.sh`.
2. In stack B: `./up.sh` (its data is untouched unless you deleted its
   volumes).
3. Restore the previous reverse-proxy configuration.

The new database columns are harmless to the old setup: with a single
contest id they are ignored. To get back the exact pre-migration database,
restore the `backup-*.sql` taken in step 1.
