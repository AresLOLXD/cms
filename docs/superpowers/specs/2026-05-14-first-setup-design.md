# First-Setup: cmsSetupDB

**Date:** 2026-05-14
**Status:** Approved

## Problem

After `cmsInitDB` creates the database schema, there is no admin account. Without one, the AdminWebServer is inaccessible and nothing can be configured. The existing workaround (`cmsAddAdmin <user>`) is a separate, undiscoverable step that is easy to forget. In Docker deployments there is no interactive prompt at all, so credentials must come from environment variables.

## Goal

Replace the `db-init` Docker service command with a new `cmsSetupDB` script that:

1. Creates the database schema (same as `cmsInitDB`).
2. Creates the first admin account if none exists — via env vars in Docker, interactive prompt on a TTY.
3. Offers to create a minimal sample contest when running interactively.

`cmsInitDB` is left unchanged so existing automation is not broken.

## Out of scope

- Modifying the admin UI or web flows.
- Importing real task data or contestants.
- Any database migration logic.

---

## Architecture

### New files

| File | Purpose |
|---|---|
| `cmscontrib/SetupDB.py` | All logic: `setup_db()`, `ensure_first_admin()`, `offer_sample_contest()` |
| `scripts/cmsSetupDB` | Thin CLI entry point, same pattern as `scripts/cmsInitDB` |

### Modified files

| File | Change |
|---|---|
| `setup.py` | Add `scripts/cmsSetupDB` to the `scripts` list |
| `docker/docker-compose.prod.yml` | `db-init` command: `["cmsInitDB"]` → `["cmsSetupDB"]` |
| `docker/.env.example` | Add `CMS_ADMIN_USER` and `CMS_ADMIN_PASSWORD` entries |

---

## Component: `cmscontrib/SetupDB.py`

### `setup_db()`

Calls `init_db()` then `ensure_first_admin()` then `offer_sample_contest()`. Returns `True` on success.

### `ensure_first_admin()`

```
If any Admin row exists in the DB → log "Admin already exists, skipping." → return.

Else:
  If CMS_ADMIN_USER and CMS_ADMIN_PASSWORD are both set in the environment:
    → create admin silently
    → log "Admin '<user>' created." (never log the password)
  Elif sys.stdin.isatty():
    → prompt "Admin username: "
    → prompt "Password: " via getpass (hidden input)
    → prompt "Confirm password: " via getpass
    → if passwords do not match → print error, re-prompt (max 3 attempts, then exit 1)
    → create admin
  Else:
    → log WARNING "No CMS_ADMIN_USER/CMS_ADMIN_PASSWORD set and no TTY available."
    → log WARNING "Run 'cmsAddAdmin <username>' manually to create the first admin."
    → return (exit 0 — non-fatal, db-init service must not fail)
```

Admin is created with `permission_all=True`, same as `cmsAddAdmin`.

### `offer_sample_contest()`

```
If not sys.stdin.isatty() → return immediately.
If any Contest row exists in the DB → return immediately.

Print: "No contests found. Create a sample contest? [y/N]: "
If answer is not "y" / "Y" → return.

Create a Contest with:
  name         = "Sample Contest"
  description  = ""
  start        = now (UTC)
  stop         = now + 24 hours (UTC)
  timezone     = "UTC"
  (all other nullable fields left at their model defaults)
```

The sample contest has no tasks and no users. Its only purpose is to give the admin a starting point visible in the AdminWebServer.

---

## Component: `scripts/cmsSetupDB`

Thin Python script following the exact pattern of `scripts/cmsInitDB`:

```python
#!/usr/bin/env python3
import gevent.monkey
gevent.monkey.patch_all()

import sys
from cms import ConfigError
from cms.db import test_db_connection
from cmscontrib.SetupDB import setup_db

def main():
    test_db_connection()
    success = setup_db()
    return 0 if success else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        import logging
        logging.getLogger(__name__).critical(error)
        sys.exit(1)
```

---

## Docker changes

### `docker-compose.prod.yml`

```yaml
db-init:
  command: ["cmsSetupDB"]   # was: ["cmsInitDB"]
```

### `.env.example` — new section after SECURITY

```ini
# -----------------------------------------------------------
# FIRST-TIME SETUP (required on first deploy)
# -----------------------------------------------------------

# Credentials for the initial admin account.
# Used by cmsSetupDB on first run. Ignored if an admin already exists.
# After the first deploy these values are no longer needed and can be removed.
CMS_ADMIN_USER=admin
CMS_ADMIN_PASSWORD=CHANGE_ME
```

---

## Error handling

| Situation | Behavior |
|---|---|
| Schema already exists | `init_db()` is idempotent — no error |
| Admin already exists | Skipped silently — no error |
| Contest already exists | Skipped silently — no error |
| Env vars partially set (only one of the two) | Log clear error, exit 1 |
| Password confirmation mismatch (interactive) | Re-prompt up to 3 times, then exit 1 |
| No TTY and no env vars | Log warning, exit 0 (non-fatal) |
| DB connection failure | `test_db_connection()` raises `ConfigError`, exit 1 |

---

## Testing

Unit tests in `cmstestsuite/unit_tests/cmscontrib/test_setup_db.py`.

| Test | What it covers |
|---|---|
| `test_skips_admin_if_exists` | `ensure_first_admin` returns early when admin row is present |
| `test_creates_admin_from_env` | Reads `CMS_ADMIN_USER`/`CMS_ADMIN_PASSWORD`, creates admin |
| `test_warns_when_no_tty_no_env` | No TTY, no env vars → warning logged, no admin created, returns True |
| `test_exits_on_partial_env` | Only one env var set → exits 1 |
| `test_skips_contest_if_exists` | `offer_sample_contest` returns early when contest row is present |
| `test_skips_contest_when_no_tty` | No TTY → contest creation skipped |
| `test_creates_sample_contest` | Mocked TTY input "y" → contest row created with correct fields |

---

## Implementation agents

| Step | Voltagent agent |
|---|---|
| `cmscontrib/SetupDB.py` | `voltagent-lang:python-pro` |
| `scripts/cmsSetupDB` | `voltagent-dev-exp:cli-developer` |
| Unit tests | `voltagent-qa-sec:test-automator` |
| Docker + `.env.example` changes | `voltagent-infra:docker-expert` |
| Final code review | `voltagent-qa-sec:code-reviewer` |

Steps 1 and 2 can run in parallel. Tests run after both complete. Docker changes run after the script is working.
