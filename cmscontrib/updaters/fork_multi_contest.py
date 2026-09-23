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

"""Schema update of this fork for multi-contest rankings.

The SQL is a Python constant because Docker installs CMS non-editable
and setup.py does not package .sql files. Every statement is
idempotent, so it is safe to apply at each cmsSetupDB run.

"""

from cms.db import custom_psycopg2_connection


FORK_MULTI_CONTEST_SQL = """
CREATE TABLE IF NOT EXISTS public.ranking_groups (
    id serial PRIMARY KEY,
    name character varying NOT NULL UNIQUE,
    description character varying NOT NULL
);

ALTER TABLE public.contests
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT false;
ALTER TABLE public.contests ALTER COLUMN active DROP DEFAULT;

ALTER TABLE public.contests
    ADD COLUMN IF NOT EXISTS ranking_group_id integer;
CREATE INDEX IF NOT EXISTS ix_contests_ranking_group_id
    ON public.contests USING btree (ranking_group_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'contests_ranking_group_id_fkey'
    ) THEN
        ALTER TABLE ONLY public.contests
            ADD CONSTRAINT contests_ranking_group_id_fkey
            FOREIGN KEY (ranking_group_id)
            REFERENCES public.ranking_groups(id)
            ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END
$$;
"""


def apply_fork_multi_contest_update() -> None:
    """Apply the fork's idempotent schema update to the database.

    """
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(FORK_MULTI_CONTEST_SQL)
        conn.commit()
    finally:
        conn.close()
