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

"""Schema update of this fork for the submission limit overrides.

Upstream adds the two columns in update_from_1.5.sql, which this fork
does not run: its database already exists, and cmsSetupDB applies the
fork's updates at each start. Without them every query on submissions
fails. The SQL is a Python constant because Docker installs CMS
non-editable and setup.py does not package .sql files. It creates
exactly what cmsInitDB creates for Submission.override_time_limit and
Submission.override_mem_limit (checked by schema_diff_test).

Every change is guarded by a catalog lookup, because even an ALTER TABLE
... ADD COLUMN IF NOT EXISTS that changes nothing takes an ACCESS
EXCLUSIVE lock, and submissions is the hottest table. Once the columns
and constraints exist, which is every start but the first, the update
takes no lock on submissions.

"""

from cmscontrib.updaters.fork_ddl import apply_fork_sql


# shortcut: adding the CHECK constraints validates the existing rows
# under the table lock, which is fine for the thousands of rows of a
# contest; use NOT VALID and then VALIDATE CONSTRAINT if submissions
# becomes huge.
FORK_SUBMISSION_LIMITS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'submissions'
            AND column_name = 'override_time_limit'
    ) THEN
        ALTER TABLE public.submissions
            ADD COLUMN override_time_limit double precision;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'submissions'
            AND column_name = 'override_mem_limit'
    ) THEN
        ALTER TABLE public.submissions
            ADD COLUMN override_mem_limit bigint;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.submissions'::regclass
            AND conname = 'submissions_override_time_limit_check'
    ) THEN
        ALTER TABLE public.submissions
            ADD CONSTRAINT submissions_override_time_limit_check
            CHECK (override_time_limit > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.submissions'::regclass
            AND conname = 'submissions_override_mem_limit_check'
    ) THEN
        ALTER TABLE public.submissions
            ADD CONSTRAINT submissions_override_mem_limit_check
            CHECK (override_mem_limit > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.submissions'::regclass
            AND conname = 'submissions_override_mem_limit_check1'
    ) THEN
        ALTER TABLE public.submissions
            ADD CONSTRAINT submissions_override_mem_limit_check1
            CHECK (MOD(override_mem_limit, 1048576) = 0);
    END IF;
END
$$;
"""


def apply_fork_submission_limits_update() -> None:
    """Apply FORK_SUBMISSION_LIMITS_SQL to the configured database.

    raise (SchemaLockTimeout): if a column or a constraint is missing
        and another session keeps submissions locked for 5 s (the
        ALTER TABLE takes an ACCESS EXCLUSIVE lock); nothing is changed
        then. When nothing is missing, the update takes no lock.

    """
    apply_fork_sql(FORK_SUBMISSION_LIMITS_SQL, "submissions")
