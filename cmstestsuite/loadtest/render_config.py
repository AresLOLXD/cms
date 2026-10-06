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

"""Render the load-test cms.toml and cms_ranking.toml for one run."""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = re.compile(r"@([A-Z_]+)@")


def render(template: str, values: dict[str, str]) -> str:
    """Replace every @NAME@ marker of template with values[NAME].

    template: text containing @NAME@ markers.
    values: the replacement text of each marker name.

    return: the template with every marker replaced.

    raise (KeyError): if a marker has no value; the message names it.

    """
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError("no value for config marker @%s@" % name)
        return values[name]
    return MARKER.sub(substitute, template)


def worker_lines(count: int) -> str:
    """Return the body of the Worker = [...] array for count workers.

    count: the number of Worker shards.

    return: one TOML array line per shard, ports starting at 26000.

    """
    return "\n".join('    ["localhost", %d],' % (26000 + i)
                     for i in range(count))


def values(db_url: str, secret_key: str, rws_password: str, workers: int,
           cws: int, two_phase: bool) -> dict[str, str]:
    """Return the marker values of both templates.

    db_url: SQLAlchemy URL of the database.
    secret_key: the web server secret key (hex).
    rws_password: the RankingWebServer password.
    workers: the number of Worker shards.
    cws: the number of ContestWebServer shards.
    two_phase: the value of the fork-only two_phase_evaluation flag.

    return: the replacement text of each marker.

    """
    return {
        "DB_URL": db_url,
        "SECRET_KEY": secret_key,
        "RWS_PASSWORD": rws_password,
        "WORKERS": worker_lines(workers),
        "CWS_RPC": "\n".join('    ["localhost", %d],' % (21000 + i)
                             for i in range(cws)),
        "CWS_ADDRESSES": ", ".join(['"0.0.0.0"'] * cws),
        "CWS_PORTS": ", ".join(str(8888 + i) for i in range(cws)),
        "TWO_PHASE": "true" if two_phase else "false",
    }


def main() -> int:
    """Write cms.toml and cms_ranking.toml into the requested directory.

    return: the process exit code.

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--rws-password", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--cws", type=int, required=True)
    parser.add_argument("--two-phase", choices=("true", "false"),
                        required=True)
    args = parser.parse_args()
    vals = values(args.db_url, args.secret_key, args.rws_password,
                  args.workers, args.cws, args.two_phase == "true")
    os.makedirs(args.out_dir, exist_ok=True)
    for name in ("cms.toml", "cms_ranking.toml"):
        with open(os.path.join(HERE, "config", name + ".tmpl")) as f:
            text = render(f.read(), vals)
        with open(os.path.join(args.out_dir, name), "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
