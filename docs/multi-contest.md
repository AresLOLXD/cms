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
nothing needs restarting. The only exception is the one-time rollout of the
version that adds ranking visibility (see "Deployment" under "Hiding a ranking
(staff view)" below); after that, hiding, revealing and changing staff
passwords also apply immediately.

## Before an exam day

1. **Ranking groups → create new ranking group.** Name it with lowercase
   letters, digits, `-` or `_` (for example `olim`); the name is part of the
   scoreboard URL. Some names are reserved because the scoreboard already uses
   them (`events`, `lib`, `img`, …); the form tells you if you pick one.
2. **Each contest's page:** tick **Active** and choose its **Ranking group**,
   then save.
3. **On each group's page** (Ranking groups → the group), set up the staff view
   (see "Hiding a ranking (staff view)" below): tick **Hide ranking from the
   public**, type a **Staff password** if you want staff to log in, then press
   **Update**. Verify in a private browser window that the public URL shows the
   notice (not the scoreboard) and the staff can log in with the password.
4. Open `http://<server>:<CMS_RWS_HTTP_PORT>/<group>/` to check each
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

Also press **Regenerate** on the affected ranking groups if ProxyService, its
container or the host restarted around the time contests were moved between
ranking groups: the change may not have reached the scoreboards.

The **Root ranking** row is the scoreboard at `/`. It is only used when a
single contest is served (`CMS_CONTEST_ID=<id>`). After switching to
`CMS_CONTEST_ID=ALL`, regenerate it once to clear the old data.

## Hiding a ranking (staff view)

A ranking group can be hidden from the public while the staff keep watching
it live. On the group's page (**Ranking groups** → the group), tick **Hide
ranking from the public**, type a **Staff password** (at most 72 bytes) and
press **Update**. The group's public URL (`/<group>/`) then shows a notice
instead of the scoreboard, and the staff log in on that same page with the
staff password.

### Configuration and verification

- To reveal the ranking, untick **Hide ranking from the public** and press
  **Update**. It takes effect within seconds.
- Editing a group keeps its staff password unless you type a new one; tick
  **Remove staff password** to delete it. The page only says whether a
  password is set, never what it is.
- Changing the password logs out every staff session: the staff have to log
  in again with the new one.
- **Check after every save:** open the public URL in a private browser window
  and confirm it shows the expected state (the notice if hidden, the ranking
  if visible). While hidden, the scoreboard's data URLs (for example
  `/<group>/contests/`) answer 403.

### Hidden groups without a staff password

- A hidden group without a staff password is visible to nobody, not even the
  staff. The **Ranking groups** list flags this with a warning. The
  **Ranking groups** list also marks which groups are hidden and which have a
  password set.

### Failure mode: RWS rejects the visibility setting

If ProxyService cannot deliver the visibility settings to RankingWebServer
(for example because RWS is outdated and lacks the `/visibility` endpoint):

1. ProxyService logs a **WARNING** saying that RWS rejected the visibility of
   that group.
2. From then on, ProxyService holds back that group's data: the ranking stays
   **empty** rather than exposed.
3. To fix it:
   - Update RankingWebServer to a version that supports visibility settings
     (with the `/visibility` endpoint).
   - Save the group again in AWS (re-send the visibility settings).
   - Press **Regenerate** for that group to resend the scores held back
     meanwhile.

### Deployment

Run `cmsSetupDB` (the Docker `db-init` service runs it), then restart AWS,
ProxyService and RankingWebServer **together**. Do not restart them
separately.

### Rollback

Roll back ProxyService and RankingWebServer **together**. Rolling back only
one of them breaks the feature:

- Rolling back only RankingWebServer to a version without this feature leaves
  **every group ranking empty**, hidden or not: ProxyService sends the
  visibility of every group, the old RWS rejects it, and ProxyService then
  holds back the group's data. Do not roll back RWS alone.
- Rolling back only ProxyService leaves the hidden groups hidden (the staff
  can still log in and scores still arrive), but hiding and revealing from
  AWS stop working until ProxyService is updated again.

### Stale form

Saving a group page that was loaded before someone else changed the **Hide**
setting will overwrite that change. If others are editing groups (for example
during contest setup), **reload the page before saving** to see the latest
state.

## Limitations

- The Telegram bot serves a single contest and is not started with
  `CMS_CONTEST_ID=ALL`.
- All contests share one Admin Web Server; there are no per-contest admin
  permissions.
- Freezing a scoreboard, or hiding and revealing it on a schedule, is not
  available yet: hide and reveal it by hand, as described above.
