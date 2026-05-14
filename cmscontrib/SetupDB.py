#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""First-time database setup: schema, first admin, optional sample contest."""

import getpass
import logging
import os
import sys

from cms.db import Admin, SessionGen
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
