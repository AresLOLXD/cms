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

"""Schema update of this fork for archiving ranking groups.

Adds ranking_groups.archive_at to an existing database, since cmsSetupDB
applies the fork's updates at each start. The SQL is a Python constant
because Docker installs CMS non-editable and setup.py does not package
.sql files. It creates exactly what cmsInitDB creates for
RankingGroup.archive_at (checked by schema_diff_test).

The column is added EMPTY and never filled: an existing group must not
be archived by surprise (see the ranking-group-archiving spec).

The change is guarded by a catalog lookup, because even an ALTER TABLE
... ADD COLUMN IF NOT EXISTS that changes nothing takes an ACCESS
EXCLUSIVE lock. Once the column exists, which is every start but the
first, the update takes no lock on ranking_groups.

"""

from cmscontrib.updaters.fork_ddl import apply_fork_sql


FORK_RANKING_GROUP_ARCHIVE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ranking_groups'
            AND column_name = 'archive_at'
    ) THEN
        ALTER TABLE public.ranking_groups
            ADD COLUMN archive_at timestamp without time zone;
    END IF;
END
$$;
"""


def apply_fork_ranking_group_archive_update() -> None:
    """Apply FORK_RANKING_GROUP_ARCHIVE_SQL to the configured database.

    raise (SchemaLockTimeout): if the column is missing and another
        session keeps ranking_groups locked for 5 s (the ALTER TABLE
        takes an ACCESS EXCLUSIVE lock); nothing is changed then. When
        the column exists, the update takes no lock.

    """
    apply_fork_sql(FORK_RANKING_GROUP_ARCHIVE_SQL, "ranking_groups")
