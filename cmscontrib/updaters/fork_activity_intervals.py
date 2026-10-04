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

"""Schema update of this fork for the participant activity log.

The SQL is a Python constant because Docker installs CMS non-editable
and setup.py does not package .sql files. Every statement is
idempotent, so it is safe to apply at each cmsSetupDB run. It creates
exactly what cmsInitDB creates for ActivityInterval (checked by
schema_diff_test).

"""

from cms.db import custom_psycopg2_connection


FORK_ACTIVITY_INTERVALS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'activity_started_by'
    ) THEN
        CREATE TYPE public.activity_started_by AS ENUM (
            'login',
            'resumed'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.activity_intervals (
    id serial PRIMARY KEY,
    participation_id integer NOT NULL
        REFERENCES public.participations(id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    device_id uuid,
    ip inet NOT NULL,
    started_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    logged_out_at timestamp without time zone,
    started_by public.activity_started_by NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_activity_intervals_ip
    ON public.activity_intervals USING btree (ip);
CREATE INDEX IF NOT EXISTS ix_activity_intervals_participation_id_last_seen_at
    ON public.activity_intervals USING btree (participation_id, last_seen_at);
"""


def apply_fork_activity_intervals_update() -> None:
    """Apply FORK_ACTIVITY_INTERVALS_SQL to the configured database.

    """
    conn = custom_psycopg2_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(FORK_ACTIVITY_INTERVALS_SQL)
        conn.commit()
    finally:
        conn.close()
