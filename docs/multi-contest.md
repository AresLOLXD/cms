# Running several contests at once

One CMS deployment can serve several contests at the same time (for example two
olympiads on the same exam day), each with its own public scoreboard.

## How it works

- `CMS_CONTEST_ID=ALL` in `.env` makes the Contest Web Server serve every
  **active** contest: contestants see the list at `/` and enter a contest at
  `/<contest_name>/`. Contests that are not active are neither listed nor
  reachable.
- A **ranking group** is an independent public scoreboard. The Ranking Web
  Server serves each group at `/<group>/` (for example `/olim/` and
  `/omips/`). Every contest assigned to a group is ranked there; contests of
  the same group are ranked together (useful for day 1 + day 2).
- Evaluation is shared: all contests use the same queue and the same workers.
  Size `CMS_WORKER_COUNT` for the combined load of all simultaneous contests.

Everything below is done in the Admin Web Server and applies immediately;
nothing needs restarting.

## Before an exam day

1. **Ranking groups → create new ranking group.** Name it with lowercase
   letters, digits, `-` or `_` (for example `olim`); the name is part of the
   scoreboard URL. Some names are reserved because the scoreboard already uses
   them (`events`, `lib`, `img`, …); the form tells you if you pick one.
2. **Each contest's page:** tick **Active** and choose its **Ranking group**,
   then save.
3. Open `http://<server>:<CMS_RWS_HTTP_PORT>/<group>/` to check each
   scoreboard.

## After the exam

- Untick **Active** to hide the contest from contestants. Its scoreboard stays
  published.
- To take a contest off a scoreboard, set its ranking group to **(none)**.

## One domain per scoreboard

The Ranking Web Server listens on a single port. Point each domain at its
group with your reverse proxy; this is configured once, not per exam:

    server {
        server_name ranking.olim.example;
        location / {
            proxy_pass http://127.0.0.1:8890/olim/;
            proxy_buffering off;   # live updates (server-sent events)
        }
    }

    server {
        server_name ranking.omips.example;
        location / {
            proxy_pass http://127.0.0.1:8890/omips/;
            proxy_buffering off;
        }
    }

The contestant domains all point at the Contest Web Server; contestants pick
their contest from the list, or you link them directly to
`/<contest_name>/`.

## When a scoreboard is wrong: Regenerate

If a scoreboard does not match the database (missing or stale scores, a
contest that should not be there), go to **Ranking groups** and press
**Regenerate** next to it. The scoreboard is emptied and all of its data is
sent again; it refills within seconds. This replaces the old
`clear-ranking.sh` script.

The **Root ranking** row is the scoreboard at `/`. It is only used when a
single contest is served (`CMS_CONTEST_ID=<id>`). After switching to
`CMS_CONTEST_ID=ALL`, regenerate it once to clear the old data.

## Limitations

- The Telegram bot serves a single contest and is not started with
  `CMS_CONTEST_ID=ALL`.
- All contests share one Admin Web Server; there are no per-contest admin
  permissions.
- Hiding, freezing or password-protecting a scoreboard is not available yet.
