# First-Setup: cmsSetupDB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cmsSetupDB` script that creates the DB schema, creates the first admin (from env vars or interactive prompt), and optionally creates a sample contest.

**Architecture:** A new `cmscontrib/SetupDB.py` module holds all logic following the existing pattern of `cmscontrib/AddAdmin.py`. A thin `scripts/cmsSetupDB` CLI entry point wraps it. The `db-init` Docker service switches from `cmsInitDB` to `cmsSetupDB`. Admin credentials come from `CMS_ADMIN_USER`/`CMS_ADMIN_PASSWORD` env vars in Docker, or from an interactive prompt when a TTY is available.

**Tech Stack:** Python 3.11+, SQLAlchemy ORM (`cms.db`), `getpass`, `os.environ`, `unittest.mock`, `DatabaseMixin` for tests.

---

## File Map

| Action | Path |
|---|---|
| Create | `cmscontrib/SetupDB.py` |
| Create | `scripts/cmsSetupDB` |
| Create | `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py` |
| Modify | `setup.py` (add `scripts/cmsSetupDB` to `scripts` list) |
| Modify | `docker/docker-compose.prod.yml` (change `db-init` command) |
| Modify | `docker/.env.example` (add `CMS_ADMIN_USER`, `CMS_ADMIN_PASSWORD`) |

---

## Task 1: Write failing tests for `ensure_first_admin`

**Files:**
- Create: `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`

- [ ] **Step 1: Create the test file**

```python
#!/usr/bin/env python3

"""Tests for the SetupDB script."""

import unittest
from unittest.mock import patch, call

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Admin
from cmscommon.crypto import validate_password


class TestEnsureFirstAdmin(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _admin_count(self):
        return self.session.query(Admin).count()

    def _get_admin(self, username):
        return self.session.query(Admin).filter(Admin.username == username).one()

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_skips_if_admin_exists(self):
        """Returns True immediately when an admin already exists."""
        self.add_admin(username="existing")
        from cmscontrib.SetupDB import ensure_first_admin
        result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 1)

    def test_creates_admin_from_env(self):
        """Creates admin from CMS_ADMIN_USER / CMS_ADMIN_PASSWORD env vars."""
        from cmscontrib.SetupDB import ensure_first_admin
        env = {"CMS_ADMIN_USER": "sysadmin", "CMS_ADMIN_PASSWORD": "s3cr3t"}
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", env, clear=False):
            result = ensure_first_admin()
        self.assertTrue(result)
        self.session.expire_all()
        a = self._get_admin("sysadmin")
        self.assertTrue(validate_password(a.authentication, "s3cr3t"))
        self.assertTrue(a.permission_all)

    def test_partial_env_only_user_returns_false(self):
        """Returns False when only CMS_ADMIN_USER is set."""
        from cmscontrib.SetupDB import ensure_first_admin
        env = {"CMS_ADMIN_USER": "sysadmin"}
        with patch("sys.stdin.isatty", return_value=False), \
             patch.dict("os.environ", env, clear=False), \
             patch.dict("os.environ", {"CMS_ADMIN_PASSWORD": ""}, clear=False):
            # ensure CMS_ADMIN_PASSWORD is absent
            import os
            os.environ.pop("CMS_ADMIN_PASSWORD", None)
            result = ensure_first_admin()
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_partial_env_only_password_returns_false(self):
        """Returns False when only CMS_ADMIN_PASSWORD is set."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        try:
            with patch("sys.stdin.isatty", return_value=False), \
                 patch.dict("os.environ", {"CMS_ADMIN_PASSWORD": "s3cr3t"}, clear=False):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_no_tty_no_env_warns_and_returns_true(self):
        """Returns True (non-fatal) when no TTY and no env vars."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=False):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)

    def test_interactive_creates_admin(self):
        """Creates admin from interactive TTY prompt."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value="interadmin"), \
                 patch("getpass.getpass", side_effect=["mypassword", "mypassword"]):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertTrue(result)
        self.session.expire_all()
        a = self._get_admin("interadmin")
        self.assertTrue(validate_password(a.authentication, "mypassword"))

    def test_interactive_mismatch_3_times_returns_false(self):
        """Returns False after 3 password confirmation mismatches."""
        from cmscontrib.SetupDB import ensure_first_admin
        import os
        saved_user = os.environ.pop("CMS_ADMIN_USER", None)
        saved_pass = os.environ.pop("CMS_ADMIN_PASSWORD", None)
        try:
            with patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value="interadmin"), \
                 patch("getpass.getpass",
                       side_effect=["pw1", "bad", "pw2", "bad", "pw3", "bad"]):
                result = ensure_first_admin()
        finally:
            if saved_user is not None:
                os.environ["CMS_ADMIN_USER"] = saved_user
            if saved_pass is not None:
                os.environ["CMS_ADMIN_PASSWORD"] = saved_pass
        self.assertFalse(result)
        self.session.expire_all()
        self.assertEqual(self._admin_count(), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Verify tests fail (module not found)**

```bash
pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -v
```

Expected: `ModuleNotFoundError: No module named 'cmscontrib.SetupDB'`

---

## Task 2: Implement `ensure_first_admin`

**Files:**
- Create: `cmscontrib/SetupDB.py`

- [ ] **Step 1: Create the module**

```python
#!/usr/bin/env python3

"""First-time database setup: schema, first admin, optional sample contest."""

import getpass
import logging
import os
import sys

from cms.db import Admin, Contest, SessionGen, init_db
from cms.db.user import Group
from cmscommon.crypto import hash_password

logger = logging.getLogger(__name__)


def ensure_first_admin() -> bool:
    """Create the first admin if none exists.

    Reads credentials from CMS_ADMIN_USER / CMS_ADMIN_PASSWORD env vars,
    or prompts interactively when a TTY is available.

    return: True on success or when setup is not needed; False on error.

    """
    with SessionGen() as session:
        if session.query(Admin).count() > 0:
            logger.info("Admin already exists, skipping.")
            return True

        env_user = os.environ.get("CMS_ADMIN_USER") or ""
        env_pass = os.environ.get("CMS_ADMIN_PASSWORD") or ""

        if bool(env_user) != bool(env_pass):
            logger.error(
                "Set both CMS_ADMIN_USER and CMS_ADMIN_PASSWORD, or neither."
            )
            return False

        if env_user and env_pass:
            username, password = env_user, env_pass
        elif sys.stdin.isatty():
            username = input("Admin username: ").strip()
            for _ in range(3):
                password = getpass.getpass("Password: ")
                confirm = getpass.getpass("Confirm password: ")
                if password == confirm:
                    break
                print("Passwords do not match. Try again.")
            else:
                logger.error("Password confirmation failed 3 times.")
                return False
        else:
            logger.warning(
                "No CMS_ADMIN_USER/CMS_ADMIN_PASSWORD set and no TTY available. "
                "Run 'cmsAddAdmin <username>' to create the first admin."
            )
            return True

        admin = Admin(
            username=username,
            authentication=hash_password(password),
            name=username,
            permission_all=True,
        )
        session.add(admin)
        session.commit()
        logger.info("Admin '%s' created.", username)
        return True
```

- [ ] **Step 2: Run tests for `ensure_first_admin`**

```bash
pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py::TestEnsureFirstAdmin -v
```

Expected: all 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add cmscontrib/SetupDB.py cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py
git commit -m "feat: add ensure_first_admin to SetupDB with tests"
```

---

## Task 3: Write failing tests for `offer_sample_contest`

**Files:**
- Modify: `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`

- [ ] **Step 1: Append test class to the test file**

Add the following class at the bottom of `cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py`, before `if __name__ == "__main__":`:

```python
class TestOfferSampleContest(DatabaseMixin, unittest.TestCase):

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def _contest_count(self):
        return self.session.query(Contest).count()

    def test_skips_when_no_tty(self):
        """Returns True immediately when there is no TTY (Docker/CI)."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=False):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 0)

    def test_skips_if_contest_exists(self):
        """Returns True immediately when a contest already exists."""
        from cmscontrib.SetupDB import offer_sample_contest
        self.add_contest()
        with patch("sys.stdin.isatty", return_value=True):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 1)

    def test_creates_sample_contest_when_confirmed(self):
        """Creates a sample contest when user answers 'y'."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 1)
        contest = self.session.query(Contest).one()
        self.assertEqual(contest.name, "sample")
        self.assertEqual(contest.description, "Sample Contest")

    def test_skips_when_declined(self):
        """Does not create a contest when user declines."""
        from cmscontrib.SetupDB import offer_sample_contest
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="N"):
            result = offer_sample_contest()
        self.assertTrue(result)
        self.session.expire_all()
        self.assertEqual(self._contest_count(), 0)
```

Also add `Contest` to the imports at the top of the test file:

```python
from cms.db import Admin, Contest
```

- [ ] **Step 2: Verify tests fail**

```bash
pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py::TestOfferSampleContest -v
```

Expected: `AttributeError: module 'cmscontrib.SetupDB' has no attribute 'offer_sample_contest'`

---

## Task 4: Implement `offer_sample_contest`

**Files:**
- Modify: `cmscontrib/SetupDB.py`

- [ ] **Step 1: Add `offer_sample_contest` to `cmscontrib/SetupDB.py`**

Add after `ensure_first_admin` and before any `setup_db` function:

```python
def offer_sample_contest() -> bool:
    """Offer to create a minimal sample contest when running interactively.

    Skips silently when there is no TTY or a contest already exists.

    return: Always True (this step is never fatal).

    """
    if not sys.stdin.isatty():
        return True

    with SessionGen() as session:
        if session.query(Contest).count() > 0:
            return True

        answer = input(
            "No contests found. Create a sample contest? [y/N]: "
        ).strip()
        if answer.lower() != "y":
            return True

        group = Group(name="Default")
        contest = Contest(
            name="sample",
            description="Sample Contest",
            groups=[group],
            main_group=group,
        )
        session.add(contest)
        session.commit()
        logger.info("Sample contest 'sample' created.")
    return True
```

- [ ] **Step 2: Run all SetupDB tests**

```bash
pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add cmscontrib/SetupDB.py cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py
git commit -m "feat: add offer_sample_contest to SetupDB with tests"
```

---

## Task 5: Wire up `setup_db()`, CLI script, and `setup.py`

**Files:**
- Modify: `cmscontrib/SetupDB.py` (add `setup_db`)
- Create: `scripts/cmsSetupDB`
- Modify: `setup.py`

- [ ] **Step 1: Add `setup_db()` to `cmscontrib/SetupDB.py`**

Append at the bottom of `cmscontrib/SetupDB.py`:

```python
def setup_db() -> bool:
    """Initialize DB schema, create first admin, offer sample contest.

    return: True on success, False if a required step failed.

    """
    init_db()
    if not ensure_first_admin():
        return False
    offer_sample_contest()
    return True
```

- [ ] **Step 2: Create `scripts/cmsSetupDB`**

```python
#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

"""Initialize the CMS database and create the first admin account."""

import gevent.monkey
gevent.monkey.patch_all()  # noqa

import logging
import sys

from cms import ConfigError
from cms.db import test_db_connection
from cmscontrib.SetupDB import setup_db


logger = logging.getLogger(__name__)


def main():
    """Parse arguments and perform operation."""
    test_db_connection()
    success = setup_db()
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        logger.critical(error)
        sys.exit(1)
```

- [ ] **Step 3: Make the script executable**

```bash
chmod +x scripts/cmsSetupDB
```

- [ ] **Step 4: Register in `setup.py`**

In `setup.py`, find the `scripts` list (line ~119) and add the new entry:

```python
"scripts/cmsSetupDB",
```

The list should look like:

```python
scripts=[
    "scripts/cmsInitDB",
    "scripts/cmsSetupDB",   # ← add this line
    ...
]
```

- [ ] **Step 5: Verify the full test suite still passes**

```bash
pytest cmstestsuite/unit_tests/cmscontrib/SetupDBTest.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add cmscontrib/SetupDB.py scripts/cmsSetupDB setup.py
git commit -m "feat: add cmsSetupDB script with setup_db orchestration"
```

---

## Task 6: Docker and `.env.example` changes

**Files:**
- Modify: `docker/docker-compose.prod.yml`
- Modify: `docker/.env.example`

- [ ] **Step 1: Update `docker-compose.prod.yml`**

In `docker/docker-compose.prod.yml`, change the `db-init` service command from:

```yaml
command: ["cmsInitDB"]
```

to:

```yaml
command: ["cmsSetupDB"]
```

- [ ] **Step 2: Add admin credentials to `.env.example`**

In `docker/.env.example`, add the following section after the `# SECURITY` block (after the `CMS_SECRET_KEY` line):

```ini
# -----------------------------------------------------------
# FIRST-TIME SETUP (required on first deploy, optional after)
# -----------------------------------------------------------

# Credentials for the initial admin account.
# Used by cmsSetupDB on first run. Ignored if an admin already exists.
# After the first deploy these values are no longer needed and can be removed.
CMS_ADMIN_USER=admin
CMS_ADMIN_PASSWORD=CHANGE_ME
```

- [ ] **Step 3: Commit**

```bash
git add docker/docker-compose.prod.yml docker/.env.example
git commit -m "feat: switch db-init to cmsSetupDB and document admin env vars"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| New script `cmsSetupDB` | Task 5 |
| `ensure_first_admin`: skip if admin exists | Task 1 + 2 |
| `ensure_first_admin`: env vars (Docker path) | Task 1 + 2 |
| `ensure_first_admin`: interactive TTY prompt | Task 1 + 2 |
| `ensure_first_admin`: partial env vars → exit 1 | Task 1 + 2 |
| `ensure_first_admin`: no TTY, no env → warning, exit 0 | Task 1 + 2 |
| `ensure_first_admin`: 3 mismatches → exit 1 | Task 1 + 2 |
| `offer_sample_contest`: skip if no TTY | Task 3 + 4 |
| `offer_sample_contest`: skip if contest exists | Task 3 + 4 |
| `offer_sample_contest`: create on "y" | Task 3 + 4 |
| `offer_sample_contest`: skip on decline | Task 3 + 4 |
| `setup_db()` orchestration | Task 5 |
| `docker-compose.prod.yml` updated | Task 6 |
| `.env.example` documented | Task 6 |
| `setup.py` registered | Task 5 |

All spec requirements covered. No placeholders or TBDs.
