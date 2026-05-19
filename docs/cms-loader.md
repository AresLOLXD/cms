# CMS-Loader

[CMS-Loader](https://github.com/AresLOLXD/CMS-Loader) is a browser-based tool
for bulk-importing users and contest participations via CSV. It is bundled
directly inside the CMS Docker image and managed by supervisord alongside the
other CMS services.

## Enabling CMS-Loader

CMS-Loader is **opt-in** — it only starts if the three required credentials are
set in `.env`. If any of them is missing the service is silently skipped and no
error is raised.

Open `.env` and fill in:

```env
# Random 32+ character string to sign session cookies.
# Generate one with: openssl rand -base64 32
CMS_LOADER_SESSION_SECRET=<your-secret>

# Administrator credentials for the CMS-Loader web UI.
CMS_LOADER_ADMIN_USER=<username>
CMS_LOADER_ADMIN_PASSWORD=<password>

# Port where CMS-Loader listens (default: 9995).
# CMS_LOADER_PORT=9995
```

Rebuild and restart the container after changing these values:

```bash
./restart.sh
# Answer "y" to "Rebuild image?" so the new env vars are picked up.
```

## Accessing CMS-Loader

Once running, open `http://your-server:9995` in your browser and log in with
the credentials you set above.

## Importing users

CMS-Loader expects a CSV file with **one row per user**. The required columns
are:

| Column | Description |
|--------|-------------|
| `username` | Login username (no spaces) |
| `password` | Initial password |
| `first_name` | First name |
| `last_name` | Last name |

Save your spreadsheet as CSV (UTF-8), then use the **Import Users** form in the
CMS-Loader UI to upload it.

## Importing participations

A participation links a user to a contest. The required columns are:

| Column | Description |
|--------|-------------|
| `username` | Must match an existing user |
| `contest_id` | Numeric ID shown in the Admin interface |
| `team` | Team code (e.g. `JAL`). Must match an existing team. |

Teams are managed in the Admin interface (`http://your-server:8889`). Find the team codes there before preparing your participation CSV.

Use the **Import Participations** form in the UI to upload the CSV.

On success, a confirmation message is shown in the UI. Users can then log in immediately via the Contest Web Server at `http://your-server:8888`.

## Version pinning

By default the image is built from the `main` branch of CMS-Loader. To pin to
a specific release, set `CMS_LOADER_VERSION` in `.env` before building:

```env
CMS_LOADER_VERSION=v1.0.0
```

This is a **build-time argument** — changing it requires a full image rebuild
(`./restart.sh` → answer `y` to "Rebuild image?").

## Troubleshooting

**CMS-Loader is not accessible on port 9995:**
Check that `CMS_LOADER_SESSION_SECRET`, `CMS_LOADER_ADMIN_USER`, and
`CMS_LOADER_ADMIN_PASSWORD` are all set in `.env`. If any is missing the
service does not start. Verify with:

```bash
./logs.sh | grep -i loader
```

**Port conflict:**
Change `CMS_LOADER_PORT` in `.env` and rebuild.
