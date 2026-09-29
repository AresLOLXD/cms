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
   If the ranking must stay hidden, tick **Hide ranking from the public** and
   type a **Staff password** in this same form (see "Hiding a ranking (staff
   view)" below). Do it now, before step 2: as soon as a contest is assigned
   to a visible group, its contestants' names and its task titles are public.
2. **Each contest's page:** tick **Active** and choose its **Ranking group**,
   then save.
3. **Check each hidden group** in a private browser window: the public URL
   shows the notice (not the scoreboard), and the staff can log in with the
   password.
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

Hiding works only with ranking groups (`CMS_CONTEST_ID=ALL`). With a single
contest (`CMS_CONTEST_ID=<id>`) the scores go to the root ranking at `/`,
which cannot be hidden: the checkbox then has no effect.

### Configuration and verification

- To reveal the ranking, untick **Hide ranking from the public** and press
  **Update**. It takes effect within seconds.
- Editing a group keeps its staff password unless you type a new one; tick
  **Remove staff password** to delete it. The page only says whether a
  password is set, never what it is.
- Typing a password, even the same one again, logs out every staff session:
  the staff have to log in again.
- Before revealing a group, press **Regenerate** on it if its name was used
  before by a group that was deleted, or if contests were moved out of it
  while ProxyService was down. The scoreboard may still hold that older data,
  and revealing the group would publish it.
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

1. ProxyService logs a **WARNING**: "Ranking … rejected the visibility of
   group …, so its data is held back".
2. From then on, ProxyService holds back that group's new data: the
   scoreboard stops updating. What the public sees depends on RWS: an
   outdated RWS does not know about hidden groups at all, so it keeps serving
   the data it already has **to everyone**, even for a group that should be
   hidden.
3. To fix it:
   - Update RankingWebServer to a version that supports visibility settings
     (with the `/visibility` endpoint).
   - Save the group again in AWS to re-send the visibility settings at once.
     ProxyService also re-sends them by itself within about 6 minutes.
   - Press **Regenerate** for that group to resend the scores held back
     meanwhile.

### Deployment

Update RankingWebServer **first**, then the rest:

1. `./up.sh` and choose **Ranking only**. Wait until it is up.
2. `./up.sh` and choose **CMS only**. This runs `cmsSetupDB` (the `db-init`
   service), which adds the new columns, and restarts AWS and ProxyService.
3. Check the ProxyService log (`./logs.sh`) for "rejected the visibility".
   There should be none. If there is, see "Failure mode" above.

Don't rebuild both at once (**All services**). The two containers then
restart at the same time, and the new ProxyService may reach the old
RankingWebServer, which rejects the visibility of every group and freezes
every scoreboard until each group is saved again and regenerated.

Deploy before the public first opens the rankings. Older versions of
RankingWebServer served the ranking page without cache headers. A browser
that cached it back then may keep showing that copy (a scoreboard with no
data) after the group is hidden, until the copy expires. A reload fixes it:
ask the staff to reload the ranking page once after the deploy.

### Rollback

**Roll back only the CMS container** (`./up.sh` → **CMS only**, from the
older checkout). **Never roll back RankingWebServer while a group is
hidden.**

- A RankingWebServer without this feature ignores the hidden setting: it
  serves every group's scoreboard to everyone, including all the data
  gathered while the group was hidden. If you must roll it back, first stop
  the `ranking` container, or reveal the groups on purpose.
- The new RankingWebServer works with an older CMS container. It keeps the
  hidden groups hidden (it stores their setting on disk), the staff can
  still log in, and scores still arrive. What stops working is hiding and
  revealing from AWS: use the command below instead.
- With an older CMS container, **Add ranking group** in AWS fails (the new
  `hidden` column has no default). Editing existing groups still works.
  Create the groups you need before rolling back.

To hide or reveal a group by hand (for example after a rollback), send the
setting straight to RankingWebServer. Run this on the server, from the
repository root: it reads the ranking credentials from `.env` and should
print `204`.

    RWS_USER=$(grep '^CMS_RWS_USERNAME=' .env | cut -d= -f2-)
    RWS_PASS=$(grep '^CMS_RWS_PASSWORD=' .env | cut -d= -f2-)
    RWS_PORT=$(grep '^CMS_RWS_HTTP_PORT=' .env | cut -d= -f2-)
    curl -s -o /dev/null -w '%{http_code}\n' \
        -u "$RWS_USER:$RWS_PASS" -X PUT \
        -H 'Content-Type: application/json' \
        -d '{"hidden": true, "staff_password": null}' \
        "http://127.0.0.1:${RWS_PORT:-8890}/<group>/visibility"

- `"hidden": false` reveals the group. A `401` means the credentials are
  wrong (if a value is quoted in `.env`, drop the quotes).
- `"staff_password": null` means no staff login. To keep it, put the
  group's stored hash instead, the `staff_password` value of its row in the
  `ranking_groups` table (it starts with `bcrypt:`). Keep the `-d` body in
  single quotes: the hash contains `$`.
- **The manual setting only lasts while the running CMS container is a
  version without ranking visibility.** A version with it (the current one,
  or an older one that already has it) sends the setting stored in AWS
  again at every save and at every sweep, within about 6 minutes, and that
  replaces the manual one. With such a version, change the setting in AWS;
  use `curl` only for the immediate effect, together with the same change
  in AWS.
- When the current CMS container is back after a rollback, check every
  group in AWS right away: the settings stored there replace the manual
  ones.

### Stale form

The **Hide** checkbox is only applied when you change it. If someone else
hid or revealed the group after you opened its page, saving the page keeps
their change. The other fields of the page still overwrite: if others are
editing groups (for example during contest setup), **reload the page before
saving**.

## Limitations

- The Telegram bot serves a single contest and is not started with
  `CMS_CONTEST_ID=ALL`.
- All contests share one Admin Web Server; there are no per-contest admin
  permissions.
- Freezing a scoreboard, or hiding and revealing it on a schedule, is not
  available yet: hide and reveal it by hand, as described above.
