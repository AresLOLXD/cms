# Running several contests at once

One CMS deployment can serve several contests at the same time (for example two
olympiads on the same exam day), each with its own public scoreboard.

## How it works

- `CMS_CONTEST_ID=ALL` in `.env` makes the Contest Web Server serve every
  **active** contest: contestants see the list at `/` and enter a contest at
  `/<contest_name>`, without a trailing slash (`/<contest_name>/` is a 404).
  Contests that are not active are neither listed nor reachable.
- A **ranking group** is an independent public scoreboard. The Ranking Web
  Server serves each group at `/<group>/` (for example `/olim/` and
  `/omips/`). Every contest assigned to a group is ranked there; contests of
  the same group are ranked together (useful for day 1 + day 2).
- Evaluation is shared: all contests use the same queue and the same workers.
  Size `CMS_WORKER_COUNT` for the combined load of all simultaneous contests.

Everything below is done in the Admin Web Server and applies immediately;
nothing needs restarting. The only exception is the one-time rollout of the
version that adds ranking visibility and the hide and freeze schedule (see
"Deployment" under "Hiding and freezing a ranking (staff view)" below); after
that, hiding, revealing, freezing, unfreezing (by hand or by schedule) and
changing staff passwords also apply immediately.

## Before an exam day

1. **Ranking groups → create new ranking group.** Name it with lowercase
   letters, digits, `-` or `_` (for example `olim`); the name is part of the
   scoreboard URL. Some names are reserved because the scoreboard already uses
   them (`events`, `lib`, `img`, …); the form tells you if you pick one.
   If the ranking must be hidden or frozen, set the hide or freeze windows in
   this same form: the four time fields (in UTC) and a **Staff password** (see
   "Hiding and freezing a ranking (staff view)" below). To hide the ranking
   from the start, put a date well in the past in **Hide from**, for example
   `2000-01-01 00:00:00` (do not type the local time "now": the field is in
   UTC, so a local "now" typed east of UTC is in the future), or press
   **Ocultar ahora** on the group's page after creating it. Do it now, before
   step 2: as soon as a contest is assigned to a visible group, its
   contestants' names and its task titles are public.
2. **Each contest's page:** tick **Active** and choose its **Ranking group**,
   then save.
3. **Check each hidden or scheduled group** in a private browser window: the
   public URL shows the notice (not the scoreboard) while hidden, and the
   staff can log in with the password. The staff can log in only while the
   group is hidden or frozen: on a visible group `/<group>/staff-login` is not
   served, so a staff login cannot be checked before the schedule starts. For
   a schedule, follow "Checking a schedule" below.
4. Open `http://<server>:<CMS_RWS_HTTP_PORT>/<group>/` to check each
   scoreboard.

## On the exam day

The step-by-step checklist is [contest-day.md](contest-day.md). The points
that matter most with several contests at once:

- The workers and the Contest Web Server processes serve all the
  simultaneous contests: size `CMS_WORKER_COUNT` and `CMS_CWS_COUNT` for the
  combined load, and keep the Contest Web Server port range clear of the
  Admin and Ranking ports.
- Publish a final ranking only once the evaluation has finished: on the
  contest's **Overview**, **Queue status** reads "Queue empty." and
  **Submissions status** has no **Compiling...**, **Evaluating...** or
  **Scoring...** row left. After the end this can take a few minutes.

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
`/<contest_name>`, without a trailing slash.

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

## Removing an old scoreboard

Renaming or deleting a ranking group leaves its old scoreboard on the
Ranking Web Server: `/<old name>/` is still served, empty, with the last hide
or freeze setting it had, and its directory (teams, flags, faces, logo,
visibility) stays on disk. If ProxyService was down when the group was
renamed or deleted, the old scoreboard may even keep its data. Nothing in the
Admin Web Server removes it. To remove it, delete its directory and restart
the ranking container. Set `OLD` to the old group name and `PROJECT` to
`CMS_PROJECT_NAME` from `.env` (`cms-prod` if it is unset); the check stops
the command if `OLD` is empty, which would delete every scoreboard:

    OLD=olim
    PROJECT=cms-prod
    [ -n "$OLD" ] && docker compose -f docker/docker-compose.prod.yml \
        --env-file .env -p "$PROJECT" exec ranking \
        rm -rf -- "/home/cmsuser/cms/lib/ranking/groups/$OLD"
    docker compose -f docker/docker-compose.prod.yml --env-file .env \
        -p "$PROJECT" restart ranking

Both steps are needed: the Ranking Web Server keeps the scoreboard in memory
until it restarts, and it loads every directory again when it starts.
Restarting it reloads the other groups from disk; their open pages reconnect or
reload by themselves. Do it outside a contest.

Then open `/<old name>/` on the Ranking Web Server: it must answer 404. If it
is back, ProxyService still had an update queued for the old name and
recreated it; it is then visible to everyone, with no data. Wait a few
minutes and remove it again.

## Hiding and freezing a ranking (staff view)

A ranking group can be **hidden** from the public while the staff keep
watching it live. It can also be **frozen**: the public keeps seeing the
scoreboard as it was at the freeze time, while the staff keep seeing it live.
Both can be done by hand, with the "… now" buttons, or scheduled with fixed
times. Nobody has to be at the screen when a scheduled time comes: the Ranking
Web Server checks the clock on every request, so restarting a service does not
disturb a schedule.

The schedule follows the clock of the server that runs the Ranking Web Server,
so keep that clock synced with NTP. A clock stepped backwards can re-open a
window that has already closed: for example, the public would see the live
scores again after the freeze.

Hiding and freezing work only with ranking groups (`CMS_CONTEST_ID=ALL`).
With a single contest (`CMS_CONTEST_ID=<id>`) the scores go to the root
ranking at `/`, which can be neither hidden nor frozen: the fields and buttons
then have no effect.

### Windows

On the group's page (**Ranking groups** → the group) there are four time
fields, each optional, in two windows:

- **Hide from (UTC)** and **Show again at (UTC)**: the public scoreboard is
  hidden between them. The group's public URL (`/<group>/`) then shows a
  notice ("Este ranking está oculto por ahora.") instead of the scoreboard,
  with the staff login form.
- **Freeze at (UTC)** and **Unfreeze at (UTC)**: the public scoreboard is
  frozen between them.

The rules:

- A window is `[start, end)`: it is open from its start time up to, but not
  including, its end time. An empty start means the window never opens. An
  empty end means it never closes.
- **Hidden wins over frozen.** A group that is hidden and inside its freeze
  window shows as "oculto (congelado)" and serves the hidden notice. If it is
  shown again while the freeze window is still open, the public gets the
  frozen scoreboard, not the live one.
- A time in the past is allowed and applies at once.
- An end must be after its start. Otherwise the page shows an "Invalid
  field(s)" notification that reads `ValueError('The hide window must end
  after it starts.')` (or `ValueError('The freeze window must end after it
  starts.')`) and saves nothing. AWS shows every error of this kind as
  Python prints it, with the `ValueError(...)` around the message.
- Type each time as `YYYY-MM-DD HH:MM:SS`. Clear a field to remove its time.
- The form to create a group has the same four fields, but not the "… now"
  buttons.

**Enter the times in UTC.** Every time field is in UTC, like the rest of AWS: a
time typed as 13:00 is 13:00 UTC, not 13:00 where the contest is. After you
save, the page shows the equivalent in the server's time zone (`CMS_TIMEZONE`)
next to each field, for example "= 2026-10-10 13:00 CST", and in the "next:"
line described below. Always check it against the contest's start and end.
This check works only if `CMS_TIMEZONE` in `.env` is set to the contest's time
zone (for example `America/Mexico_City`). If it is unset, it defaults to UTC:
the local equivalent then equals the UTC value, and comparing them catches
nothing. Set `CMS_TIMEZONE` before you schedule anything; it applies when the
containers are recreated (`./up.sh`).

### What the group page shows

The **Now** row shows the state at the moment the page was loaded, in bold:
"visible", "oculto" (hidden), "congelado" (frozen) or "oculto (congelado)". If
one of the times is still ahead, the row adds "next:" and the nearest one, for
example:

    congelado · next: Unfreeze at: 2026-10-10 21:00:00 UTC (2026-10-10 15:00 CST)

The **Ranking groups** list shows the same in its **State** column.

### The "now" buttons

Under the fields, the page shows one button of each pair, the one that
applies:

- **Ocultar ahora** (shown while the group is not hidden) sets **Hide from**
  to now. It keeps a **Show again at** that is still ahead, so the ranking
  comes back at that time, and clears one that is already past.
- **Mostrar ahora** (shown while hidden) sets **Show again at** to now.
  Refused with `ValueError('The ranking is not hidden.')` if the group is not
  hidden.
- **Congelar ahora** (shown while no freeze is open) sets **Freeze at** to
  now. It keeps an **Unfreeze at** that is still ahead and clears one that is
  already past. Refused with `ValueError('The ranking is already frozen.')` if
  a freeze is already open, so the freeze time can never be moved later by
  accident.
- **Descongelar ahora** (shown while a freeze is open) sets **Unfreeze at** to
  now. Refused with `ValueError('The ranking is not frozen.')` if no freeze is
  open.

A refusal shows an "Invalid field(s)" notification with that message, in the
form shown above, and saves nothing.

The freeze buttons follow the freeze window, even while the group is hidden:
on a hidden group whose freeze window is open, the page offers **Descongelar
ahora**, never **Congelar ahora**.

The line under the buttons says: "Un botón «… ahora» reemplaza la hora escrita
en ese campo." A "now" button replaces the time typed in its own field, and
saves the rest of the page as **Update** does. Pressing Enter in a field saves
the form like **Update**; it does not press a "now" button.

### While the ranking is frozen

The public sees:

- The scoreboard as it was at the freeze time, to the second. Submissions made
  before the freeze count, with their result even if they are evaluated
  later. Submissions made after the freeze, and token uses after it, do not
  count. A score change within the same second as the freeze may still
  appear.
- With **Congelar ahora**, the freeze reaches the Ranking Web Server a few
  seconds after the button is pressed, but the snapshot is as of the moment
  the button was pressed. A score that the public saw live in those seconds
  disappears from the page when it reloads.
- A bar at the bottom of the page: "Ranking congelado desde las HH:MM
  (\<zone>)", with the freeze time in the server's time zone, and an "Acceso
  staff" link.
- No live score updates. A late evaluation of a submission made before the
  freeze appears when the public reloads the page.
- On the data URLs: `/<group>/scores`, `/<group>/history` and
  `/<group>/sublist/<user>` answer with the data as of the freeze;
  `/<group>/events` keeps sending contest, task, team and user changes, but no
  scores; `/<group>/submissions`, `/<group>/subchanges` and any path the
  scoreboard does not use answer 403. The page, its static files,
  `/<group>/contests`, `/<group>/tasks`, `/<group>/teams`, `/<group>/users`,
  faces, flags, the logo and the config answer as usual.

The staff:

- Log in with the same password and the same cookie as for a hidden group.
  While frozen, they log in from the "Acceso staff" link; while hidden, from
  the notice. They can log in only while the group is hidden or frozen: on a
  visible group `/<group>/staff-login` is not served. The staff cookie
  persists afterwards: a login made while the group is hidden or frozen is
  still valid the next time it is hidden or frozen, until the staff press
  "Salir", the browser session ends or the staff password changes.
- See everything live. The page carries the bar "Vista staff: ranking
  congelado para el público" (on a hidden group, "Vista staff: este ranking
  está oculto al público"), with a "Salir" link to log out. A group that is
  hidden and frozen shows the hidden notice and the hidden bar.
- A frozen group without a staff password has no live view for anybody. The
  **Ranking groups** list does not flag it (see "Hidden groups without a staff
  password" below).

When the public view of a group changes (it is hidden, shown, frozen or
unfrozen, by hand or at a scheduled time), an open scoreboard page reloads by
itself within about 15 seconds (the ping interval). On a hide, it then shows
the notice. Changing a window that is still ahead changes nothing on the page
until its time comes. A page that is showing the hidden notice checks every 15
seconds, and comes back by itself when the group is shown again (or only
frozen). A page reloads itself at most once every 10 seconds.

When the Ranking Web Server restarts, the open pages of a group that is
frozen at that moment reload once. The open pages of a visible group (even one
that keeps the times of earlier windows), and of the root ranking, do not
reload: they reconnect by themselves, as before this feature.

### Checking a schedule

After saving a schedule, on the group's page:

1. Compare the local equivalents shown next to each field, and in the "next:"
   line, with the contest's start and end. If they do not match, the times
   were typed in the wrong time zone: correct them. This only works if
   `CMS_TIMEZONE` is set: if it is unset it defaults to UTC, and the local
   equivalent equals the UTC value.
2. Read the **Now** row: it must say what you expect at this moment.
3. Open the public URL (`http://<server>:<CMS_RWS_HTTP_PORT>/<group>/`) in a
   private browser window. It shows the notice if the group is hidden, the
   scoreboard with the bar "Ranking congelado desde las HH:MM (\<zone>)" if
   frozen (the time must be the freeze time you intended), and the plain
   scoreboard if visible.
4. Log in as staff (from the notice or the "Acceso staff" link) and check the
   staff bar. The staff can log in only while the group is hidden or frozen
   (on a visible group `/<group>/staff-login` is not served), so this step
   cannot be done before the schedule starts: do it when the group is first
   hidden or frozen. The staff cookie persists afterwards.

Do it again after every save, and again once a scheduled time has passed.

### Configuration and verification

- To reveal the ranking now, press **Mostrar ahora**; to unfreeze it, press
  **Descongelar ahora**. To cancel a schedule, clear its fields and press
  **Update**. It takes effect within seconds.
- Editing a group keeps its staff password unless you type a new one (at most
  72 bytes); tick **Remove staff password** to delete it. The page only says
  whether a password is set, never what it is.
- Typing a password, even the same one again, logs out every staff session:
  the staff have to log in again.
- Before revealing a group, press **Regenerate** on it if its name was used
  before by a group that was deleted, or if contests were moved out of it
  while ProxyService was down. The scoreboard may still hold that older data,
  and revealing the group would publish it. Do it before a scheduled **Show
  again at**, too.
- **Check after every save:** open the public URL in a private browser window
  and confirm it shows the expected state (the notice if hidden, the ranking
  with its freeze bar if frozen, the ranking if visible). While hidden, the
  scoreboard's data URLs (for example `/<group>/contests/`) answer 403.

### Hidden groups without a staff password

- A hidden group without a staff password is visible to nobody, not even the
  staff. The **Ranking groups** list flags this with "(nobody can see it: no
  staff password)". The **Ranking groups** list also shows the state of every
  group and which ones have a password set.
- The flag appears only on hidden groups, including "oculto (congelado)". A
  frozen group without a staff password is not flagged, although nobody has a
  live view of it either: check its **Staff password** column.

### Failure mode: RWS rejects the visibility setting

If ProxyService cannot deliver the visibility settings to RankingWebServer
(for example because RWS is outdated: it lacks the `/visibility` endpoint, or
it is from before the hide and freeze schedule and answers 400 to the new
format of the windows):

1. ProxyService logs a **WARNING**: "Ranking … rejected the visibility of
   group …, so its data is held back".
2. From then on, ProxyService holds back that group's new data: the
   scoreboard stops updating. What the public sees depends on RWS: an
   outdated RWS without `/visibility` does not know about hidden groups at
   all, so it keeps serving the data it already has **to everyone**, even for
   a group that should be hidden. An RWS from before the schedule keeps the
   last setting it accepted: a hide or freeze you scheduled never takes
   effect there.
3. To fix it:
   - Update RankingWebServer to this version (it has the `/visibility`
     endpoint and accepts the windows).
   - Save the group again in AWS to re-send the visibility settings at once.
     ProxyService also re-sends them by itself within about 6 minutes.
   - Press **Regenerate** for that group to resend the scores held back
     meanwhile.

### Deployment

Update RankingWebServer **first**, then the rest:

1. `./up.sh` and choose **Ranking only**. It rebuilds and restarts only the
   ranking container and returns once it is up; the other services are left
   alone. That is deliberate: `cms` must not be recreated from its old image
   before step 2 rebuilds it (see "Upgrading a deployment that was started
   before the `cms-data` volume moved" in [Docker deployment](docker-deployment.md)).
2. `./up.sh` and choose **CMS only**. This runs `cmsSetupDB` (the `db-init`
   service), which adds the new columns (the four times of the windows among
   them), and restarts AWS and ProxyService. A group that was hidden with the
   old **Hide ranking from the public** checkbox gets **Hide from** set to the
   time of this step, so it stays hidden.
3. Check the ProxyService log (`./logs.sh`) for "rejected the visibility".
   There should be none. If there is, see "Failure mode" above.

Don't rebuild both at once (**All services**). The two containers then
restart at the same time, and the new ProxyService may reach the old
RankingWebServer. The new ProxyService sends the windows in a format that the
old RankingWebServer rejects, so it rejects the visibility of every group and
every scoreboard stops updating until each group is saved again and
regenerated.

Deploy before the public first opens the rankings. Older versions of
RankingWebServer served the ranking page without cache headers. A browser
that cached it back then may keep showing that copy (a scoreboard with no
data) after the group is hidden, until the copy expires. A reload fixes it:
ask the staff to reload the ranking page once after the deploy.

### Rollback

**Roll back only the CMS container** (`./up.sh` → **CMS only**, from the
older checkout). **Never roll back RankingWebServer while a group is hidden
or frozen.**

Do not roll back the CMS container while a group is frozen: the older
container unfreezes it, and a freeze set with curl is undone at its next
sweep (about 6 minutes). If you must, stop the `ranking` container first.

**Only roll back to a checkout that already mounts `cms-data` on
`/home/cmsuser/cms/data`** (see the upgrade note in
[Docker deployment](docker-deployment.md)). An older
checkout mounts the volume over the installed code again, so the `cms`
container would run the stale copy left in the volume, not that checkout's
code, or not start at all if that copy was deleted. Before a contest, note
the commit you deployed and the last known-good one after the move, and
roll back to that.

- A RankingWebServer without this feature ignores the hidden setting: it
  serves every group's scoreboard to everyone, including all the data
  gathered while the group was hidden. One from before the schedule ("MC-2
  minimal" from here on: the version that added ranking visibility but not
  the schedule) rejects the windows it is sent (see "Failure mode" above), so
  it cannot freeze a group. If you must roll it back, first stop the
  `ranking` container, or reveal the groups on purpose.
- The new RankingWebServer works with an older CMS container. It keeps the
  hidden groups hidden (it stores their setting on disk), the staff can
  still log in, and scores still arrive. What stops working is hiding and
  revealing from AWS (with a CMS from before ranking visibility): use the
  command below instead.
- A CMS container from before the schedule (MC-2 minimal) sends the old
  `{"hidden": …}` setting, which has no windows. RWS accepts it: a group with
  `hidden` set becomes hidden with no end, and any other group loses its
  windows. So **while such a container runs, scheduled hides, shows and
  freezes do not happen, and a frozen group is unfrozen**, from the moment it
  sends the setting (at each save and at every sweep, within about 6
  minutes). A group that is hidden now or has a hide still ahead is hidden
  at once, because AWS rewrites `hidden` from the windows at every save.
- With an older CMS container, **Add ranking group** in AWS fails (the new
  `hidden` column has no default). Editing existing groups still works.
  Create the groups you need before rolling back.

To hide, reveal, freeze or unfreeze a group by hand (for example after a
rollback), send the setting straight to RankingWebServer. A freeze set this way
lasts only while the running CMS container is from before ranking visibility:
any later container undoes it at its next sweep (see the last bullets below).
Run this on the server, from the repository root: it reads the ranking
credentials from `.env` and should print `204`.

    RWS_USER=$(grep '^CMS_RWS_USERNAME=' .env | cut -d= -f2-)
    RWS_PASS=$(grep '^CMS_RWS_PASSWORD=' .env | cut -d= -f2-)
    RWS_PORT=$(grep '^CMS_RWS_HTTP_PORT=' .env | cut -d= -f2-)
    curl -s -o /dev/null -w '%{http_code}\n' \
        -u "${RWS_USER:-rws}:$RWS_PASS" -X PUT \
        -H 'Content-Type: application/json' \
        -d '{"hide_at": 0, "show_at": null, "freeze_at": null, "unfreeze_at": null, "staff_password": null}' \
        "http://127.0.0.1:${RWS_PORT:-8890}/<group>/visibility"

- The body is `{"hide_at": <unix seconds or null>, "show_at": …,
  "freeze_at": …, "unfreeze_at": …, "staff_password": …}`. All five keys are
  required. The times are the same windows as in AWS, in Unix seconds (for
  example `date -d '2026-10-10 19:00:00 UTC' +%s`), or `null`.
- `"hide_at": 0` hides the group immediately and with no end. To reveal it,
  send all four times as `null`. To freeze it, put the freeze time in
  `"freeze_at"`.
- RWS answers `400` if an end is not after its start, if a time is not an
  integer, or if the body mixes the two formats. A `401` means the
  credentials are wrong (if a value is quoted in `.env`, drop the quotes).
- RWS still accepts the old body `{"hidden": …, "staff_password": …}` of
  MC-2 minimal: `"hidden": true` means `"hide_at": 0` with the other times
  `null`, and `"hidden": false` reveals the group and clears every time.
- `"staff_password": null` means no staff login. To keep it, put the
  group's stored hash instead, the `staff_password` value of its row in the
  `ranking_groups` table (it starts with `bcrypt:`). Keep the `-d` body in
  single quotes: the hash contains `$`.
- **The manual setting only lasts while the running CMS container is a
  version without ranking visibility.** A version with it (the current one,
  or an older one that already has it) sends the setting stored in AWS
  again at every save and at every sweep, within about 6 minutes, and that
  replaces the manual one. That includes a freeze: the version from before the
  schedule sends no windows, so it undoes a freeze set with `curl`. With such
  a version, change the setting in AWS; use `curl` only for the immediate
  effect, together with the same change in AWS.
- When the current CMS container is back after a rollback, check every
  group in AWS right away: the settings stored there replace the manual
  ones.

After rolling back to a CMS without scheduled windows (MC-2 minimal) and then
deploying this version again, **re-check every group's windows in AWS**. The
hide and unhide changes made through the older AWS are not reconciled with
the windows:

- A group unhidden through the older AWS is hidden again by its window after
  the redeploy. That errs on the safe side.
- A group hidden through the older AWS whose window had already ended stays
  public after the redeploy, from the redeploy until you press **Ocultar
  ahora** on it. Do it right after `./up.sh` returns.

### Stale form

Each time field is only applied when you change it. If someone else changed
the schedule after you opened the page (for example hid the group or set a
freeze), saving the page keeps their change: a field you did not touch does
not undo it. The other fields of the page (name, description) still
overwrite: if others are editing groups (for example during contest setup),
**reload the page before saving**. A "now" button acts on the schedule stored
at that moment plus any field you edited on that page, not on the schedule
the page showed, so it can be refused with the messages above.

## Limitations

- The Telegram bot serves a single contest and is not started with
  `CMS_CONTEST_ID=ALL`.
- All contests share one Admin Web Server; there are no per-contest admin
  permissions.
- The times of a schedule are absolute. There are no times relative to the
  end of a contest (for example "60 minutes before the end"): if a contest is
  extended, change the times by hand.
- Each group has one hide window and one freeze window.
- A frozen scoreboard is the scoreboard as of the freeze time, nothing more:
  there are no ICPC-style "pending" cells and no positions-only view.
- The root ranking (`/`, single-contest mode) can be neither hidden nor
  frozen.
