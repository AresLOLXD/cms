# Participant activity

CMS keeps a log of when each participant uses the Contest Web Server, from
which device and from which IP address. Use it to check who took part and
when, and to spot an account used from two places at the same time.

It is always on: there is nothing to turn on. The Admin Web Server (AWS)
shows it and exports it as CSV; it cannot be edited there.

**The log is not in the `./export.sh` backups, and it is deleted with the
participation.** Download the CSV after the contest (see "After the
contest").

## What is recorded

- Every request a logged-in participant makes to the Contest Web Server. An
  open contest page asks the server for notifications every 30 seconds while
  the participant is logged in, so a page left open counts as activity.
- Every successful login, with the login form or the API.
- Every logout with the **Logout** button ("Salir" in Spanish).

Not recorded: failed logins, requests of someone who is not logged in, and
requests an admin makes while impersonating a participant.

The log is kept per participation, that is, per user and contest.

### Intervals

The activity is stored as **intervals**. An interval is a run of requests of
one participation from one device and one IP address, with no gap of more
than 30 minutes between them. A new interval starts when:

- the device or the IP address changes. When the IP address of a device
  changes, the log shows consecutive intervals with the same device;
- the participant logs in;
- a request comes after a logout;
- a request comes after more than 30 minutes without requests.

Each interval shows:

| Column | Meaning |
|--------|---------|
| **Device** | The browser it came from (see "The device cookie"), or "—" for none. |
| **IP address** | The address the requests came from (see "IP addresses behind a reverse proxy"). |
| **Start** | The first request. |
| **Last activity** | The last request written to the database, up to a minute behind (see "When it is written"). On a logout, the time of the logout. |
| **End** | `logout`, `inactivity` or `active` (below). |
| **Started by** | `login` or `resumed` (below). |

The **End** column says how the interval ended:

- `logout`: the participant pressed **Logout**. It ended then.
- `inactivity`: there has been no request for more than 30 minutes. It ended
  at its last activity.
- `active`: its last activity is less than 30 minutes old. This does not mean
  the participant is still there: an interval stays `active` for 30 minutes
  after its last request, even if the browser was closed.

The end is worked out each time you open the page, not stored, so an
`active` interval becomes `inactivity` later on its own.

The **Started by** column says what opened the interval:

- `login`: a login with username and password.
- `resumed`: any other request of a participant who was already logged in
  (with the login cookie, the API header or IP autologin): coming back after
  more than 30 minutes, after a logout with IP autologin, or from a new
  device or IP address.

### The device cookie

The Contest Web Server gives each browser a `cms_device` cookie with a random
id. That id is the **device**.

- The cookie is issued at a login with the login form, and to a logged-in
  browser that does not have it. It lasts one year and is shared by all the
  contests of the Contest Web Server.
- It is signed by the server: a value made up by hand is rejected, and the
  browser gets a new one.
- A device is a browser, not a computer. Another browser, a private window or
  deleted cookies count as a new device.
- AWS shows the first 8 characters of the id; hover over them to see it
  whole. The CSV has the whole id.

**A missing device is not proof of a script.** A request has no device
(shown as — in AWS, empty in the CSV) when:

- it was authenticated with the `X-CMS-Authorization` header, as API clients
  do. They never get the cookie;
- the browser did not send a valid device cookie: the first request after an
  IP autologin, the first one after deleting cookies, or a client that keeps
  no cookies. The browser gets the cookie with that response, so its next
  requests have a device, in a new interval.

## Where to see it

### The Activity page of a contest

In AWS, open the contest and click **Activity** in the menu. Any admin
account can open it. The times are in the contest's time zone: its
**Timezone** field or, if that is empty, the server's (`CMS_TIMEZONE` in
Docker).

It has three blocks:

- **Simultaneous activity (N)**: the first alert (see "Alerts").
- **More than one device (N)**: the second alert.
- **Intervals (N)**: every interval of the contest, the most recent last
  activity first, 100 per page. Filter it with **Username** (the exact
  username) and **IP address or network** (one address, or a network such as
  `10.0.0.0/24`), then **Filter**; **Clear** removes the filters. The
  intervals of the simultaneous activity listed above are highlighted.

Each username links to the participation's page.

### The participation page

On a participation's page (the contest's **Users**, then the username), the
**Activity** block lists all of its intervals, the most recent start first,
on one page. If the participation has simultaneous activity, it is listed
above them, complete, and its intervals are highlighted. Use this page to
look into one contestant.

## Alerts

### Simultaneous activity

A pair of intervals of one participation that:

- come from two different devices or, when one of them has no device, from
  two different IP addresses;
- overlap for more than 2 × `activity_flush_interval`: 120 seconds by
  default. The page states the number.

Not reported:

- an IP address change on one device: its intervals follow each other;
- an interval with a device and one without from the same IP address, such as
  the first request after an IP autologin.

Each row shows **Username**, **First device / IP**, **Second device / IP**,
**Overlap from** and **Overlap to**.

The Activity page lists at most **200** pairs, the most recent overlap first.
The heading shows the total; when there are more, the page says "Only the 200
most recent are shown". The participation page lists all of its pairs, and
the CSV has every interval.

A pair means the account was used from two browsers (or two IP addresses) at
the same time. It does not say who used them: a contestant with two browsers open, or a private
window, gives a pair too. Look at the participation page, the IP addresses
and the times before drawing a conclusion.

### More than one device

The participations seen on more than one device, with how many, most first.
Requests without a device are not counted. It is informational only: another
browser, a private window or deleted cookies also count as a new device.

## CSV export

On the Activity page, the **csv** link under the filters downloads
`activity.csv`. It uses the filters in use: press **Clear** first to get the
whole log. It has every interval, with no limit.

The columns:

| Column | Content |
|--------|---------|
| `username`, `first_name`, `last_name` | The user. |
| `device_id` | The whole device id; empty for none. |
| `ip` | The IP address. |
| `started_at` | The first request. |
| `last_seen_at` | The last activity. |
| `ended_at` | The logout time for `logout`, the last activity for `inactivity`, empty for `active`. |
| `end_reason` | `logout`, `inactivity` or `active`, at the time of the download. |
| `started_by` | `login` or `resumed`. |

The times are in UTC, in ISO 8601 with the offset, such as
`2026-10-12T10:00:00+00:00`.

If the download fails halfway, the connection is cut, so the browser reports
a failed download instead of saving a file that looks complete. Download it
again.

## IP addresses behind a reverse proxy

The IP address is the one the request came from, after skipping the number
of reverse proxies set in `CMS_NUM_PROXIES_USED` (in `.env`;
`num_proxies_used` in `cms.toml` without Docker).

- Behind nginx, set it to the number of proxies: `1` for one nginx. See
  "nginx configuration" in [Docker Scripts Guide](docker-scripts.md). With
  the default `0`, every request is recorded with the proxy's address: the IP
  addresses say nothing, and the simultaneous activity of requests without a
  device, which compares IP addresses, is never reported.
- Do not set it higher than the real number of proxies: a contestant could
  then choose the address that is recorded, with an `X-Forwarded-For` header.

## When it is written

Each Contest Web Server process keeps the activity in memory and writes it to
the database every `activity_flush_interval` seconds (60 by default). So:

- **Last activity** can be up to about a minute behind. The 2 ×
  `activity_flush_interval` margin of the simultaneous activity covers this.
- If a Contest Web Server process crashes, the activity it has not written
  yet is lost: up to a minute by default.
- When it stops normally, it writes what it has first, for at most 30
  seconds. If that fails, its log says `The participants' activity could
  not be stored within 30 seconds at shutdown; the activity of N
  participation(s) is lost.`
- If the database cannot be reached, the activity stays in memory and the
  next write tries again. The log says `Could not store the activity of N
  participation(s); the next flush retries it.`

## Settings

Two keys of the `[contest_web_server]` section of `cms.toml`:

| Key | Default | Meaning |
|-----|---------|---------|
| `activity_flush_interval` | `60` | Seconds between writes. The simultaneous activity margin is twice this. |
| `activity_inactivity_threshold` | `1800` | Seconds without requests that end an interval (the 30 minutes above). |

Without Docker, change them in `cms.toml` and restart the Contest Web Server
and the Admin Web Server: AWS uses both to show the alerts and the end of
each interval.

With Docker they cannot be changed: there is no `.env` setting for them, and
the generated `cms.toml` does not include them, so the defaults always apply.

## Keeping the log

- `./export.sh` does not include it, so `./import.sh` does not restore it.
  Importing with the database dropped first (`-d`) deletes it.
- Removing a user from a contest, or deleting the user or the contest,
  deletes the intervals of that participation. Nothing else deletes them:
  there is no automatic clean-up.
- The CSV is the only way to keep it.

## After the contest

1. Wait at least one minute after the last contestant leaves, so the last
   activity is written.
2. On each contest's **Activity** page, press **Clear**, then download the
   **csv**. Copy the files off the server with the `./export.sh` backup.
3. Read the **Simultaneous activity** block. If the heading shows more than
   200, open the participation page of each user concerned.
4. Only then remove participations, users or contests, or restore a backup
   with `./import.sh`.
