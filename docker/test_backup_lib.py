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

"""Tests for docker/_backup_lib.sh, the helpers of backup.sh."""

import os
import re
import shutil
import subprocess
import textwrap
from typing import Callable, Dict

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Answers the two docker commands the library issues and logs every
# command line to FAKE_LOG.
FAKE_DOCKER = textwrap.dedent("""\
    #!/usr/bin/env bash
    echo "$*" >>"$FAKE_LOG"
    if [[ "$1 $2" == "network ls" ]]; then echo "${FAKE_NETWORK:-}"; fi
    exit 0
    """)

# The variables parse_db_url sets, read back through printenv so that a
# missing export shows up too.
SHOW_PG = ('for v in PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE; do '
           'printf "%s=%s\\n" "$v" "$(printenv "$v")"; done')

Runner = Callable[..., subprocess.CompletedProcess]


def pg_vars(result: subprocess.CompletedProcess) -> Dict[str, str]:
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


@pytest.fixture
def repo(tmp_path):
    """A copy of docker/ without .env, so the environment decides."""
    (tmp_path / "repo" / "docker").mkdir(parents=True)
    for name in ("_lib.sh", "_backup_lib.sh"):
        src = os.path.join(REPO, "docker", name)
        if os.path.exists(src):
            shutil.copy(src, tmp_path / "repo" / "docker" / name)
    return tmp_path / "repo"


@pytest.fixture
def run(repo, tmp_path) -> Runner:
    """Return a function that runs a bash snippet with the library loaded.

    With with_lib, docker/_lib.sh is sourced first, as backup.sh does.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)

    def runner(script: str, with_lib: bool = False,
               **env: str) -> subprocess.CompletedProcess:
        prelude = "source docker/_lib.sh; " if with_lib else ""
        environment = {"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                       "HOME": str(tmp_path),
                       "FAKE_LOG": str(tmp_path / "docker.log"), **env}
        return subprocess.run(
            ["bash", "-c",
             prelude + "source docker/_backup_lib.sh; " + script],
            cwd=repo, env=environment, capture_output=True, text=True,
            timeout=30)

    return runner


def test_parse_db_url_psycopg2_scheme(run):
    for scheme in ("postgresql+psycopg2", "postgresql"):
        result = run('parse_db_url "$URL"; ' + SHOW_PG,
                     URL=scheme + "://cms:secret@db:5432/cmsdb")
        assert result.returncode == 0, result.stderr
        assert pg_vars(result) == {
            "PGHOST": "db", "PGPORT": "5432", "PGUSER": "cms",
            "PGPASSWORD": "secret", "PGDATABASE": "cmsdb"}


def test_parse_db_url_decodes_a_password_with_special_characters(run):
    cases = [
        ("p%40ss%3Aw%2Frd", "p@ss:w/rd"),
        # A raw ':' or '/' is accepted by SQLAlchemy, so by CMS.
        ("pa:ss/w", "pa:ss/w"),
        # '%25' is decoded once, and a '%' that starts no escape stays.
        ("100%25%zz%41", "100%%zzA"),
        ("ni%C3%B1o", "niño"),
    ]
    for encoded, decoded in cases:
        result = run('parse_db_url "$URL"; ' + SHOW_PG,
                     URL="postgresql://cms:%s@db/cmsdb" % encoded)
        assert result.returncode == 0, result.stderr
        # The decoding must not disturb the other parts of the URL.
        assert pg_vars(result) == {
            "PGHOST": "db", "PGPORT": "5432", "PGUSER": "cms",
            "PGPASSWORD": decoded, "PGDATABASE": "cmsdb"}
    result = run('parse_db_url "$URL"; ' + SHOW_PG,
                 URL="postgresql://us%40er:pw@db/cmsdb")
    assert pg_vars(result)["PGUSER"] == "us@er"
    assert pg_vars(result)["PGHOST"] == "db"


def test_parse_db_url_default_port_and_query_suffix(run):
    result = run('parse_db_url "$URL"; ' + SHOW_PG,
                 URL="postgresql://cms:pw@db/cmsdb?sslmode=require&a=b")
    assert pg_vars(result)["PGPORT"] == "5432"
    assert pg_vars(result)["PGDATABASE"] == "cmsdb"
    result = run('parse_db_url "$URL"; ' + SHOW_PG,
                 URL="postgresql://cms:pw@10.0.0.5:6543/cmsdb?x=y")
    assert pg_vars(result)["PGHOST"] == "10.0.0.5"
    assert pg_vars(result)["PGPORT"] == "6543"


@pytest.mark.parametrize("url", [
    "",
    "mysql://cms:pw@db/cmsdb",
    "postgresql://db/cmsdb",
    "postgresql://cms:pw@db",
    "postgresql://cms:pw@db/",
    "postgresql://cms:pw@db:port/cmsdb",
    # A raw '@' in the password must fail, not connect to the host "ss".
    "postgresql://cms:p@ss@db/cmsdb",
])
def test_parse_db_url_rejects_empty_and_malformed(run, url):
    result = run('parse_db_url "$URL"; rc=$?; '
                 'echo "${PGHOST-unset}"; exit $rc', URL=url)
    assert result.returncode != 0
    assert "ERROR" in result.stderr
    # The URL holds the password, so it is not echoed.
    assert url == "" or url not in result.stderr
    assert "pw" not in result.stderr
    # Nothing is half-set.
    assert result.stdout.strip() == "unset"


def test_db_connection_from_env_localdb(run):
    env = dict(CMS_USE_LOCALDB="true", POSTGRES_USER="cmsu",
               POSTGRES_PASSWORD="p%40w", POSTGRES_DB="cmsdb2",
               CMS_DB_URL="postgresql://other:x@elsewhere/other")
    result = run("db_connection_from_env; " + SHOW_PG, with_lib=True, **env)
    assert result.returncode == 0, result.stderr
    # POSTGRES_PASSWORD is literal, not URL-encoded.
    assert pg_vars(result) == {
        "PGHOST": "db", "PGPORT": "5432", "PGUSER": "cmsu",
        "PGPASSWORD": "p%40w", "PGDATABASE": "cmsdb2"}
    del env["POSTGRES_PASSWORD"]
    result = run("db_connection_from_env", with_lib=True, **env)
    assert result.returncode != 0
    assert "POSTGRES_" in result.stderr


def test_db_connection_from_env_external_url(run, repo):
    # The value comes from .env, as it does on the server.
    (repo / ".env").write_text(
        'CMS_DB_URL="postgresql+psycopg2://cms:p%40ss@10.0.0.5:5433/prod"\n'
        "POSTGRES_USER=ignored\n")
    result = run("db_connection_from_env; " + SHOW_PG, with_lib=True,
                 CMS_USE_LOCALDB="false")
    assert result.returncode == 0, result.stderr
    assert pg_vars(result) == {
        "PGHOST": "10.0.0.5", "PGPORT": "5433", "PGUSER": "cms",
        "PGPASSWORD": "p@ss", "PGDATABASE": "prod"}
    (repo / ".env").unlink()
    result = run("db_connection_from_env", with_lib=True)
    assert result.returncode != 0
    assert "ERROR" in result.stderr


@pytest.mark.parametrize("version_num, major", [
    ("160015", "17"), ("170002", "17"), ("180001", "18"), ("90624", "17")])
def test_pick_pg_major(run, version_num, major):
    result = run('pick_pg_major "$VERSION"', VERSION=version_num)
    assert result.returncode == 0, result.stderr
    assert result.stdout == major + "\n"


@pytest.mark.parametrize("version_num", ["", "abc", "17.2"])
def test_pick_pg_major_rejects_a_non_number(run, version_num):
    result = run('pick_pg_major "$VERSION"', VERSION=version_num)
    assert result.returncode != 0
    assert "ERROR" in result.stderr


def test_backup_name_with_and_without_label(run):
    result = run("backup_name")
    assert re.fullmatch(r"cms-20\d{6}-\d{6}\n", result.stdout)
    result = run('backup_name ""')
    assert re.fullmatch(r"cms-20\d{6}-\d{6}\n", result.stdout)
    result = run("backup_name pre-Contest_1")
    assert re.fullmatch(r"cms-20\d{6}-\d{6}-pre-Contest_1\n", result.stdout)


@pytest.mark.parametrize("label", ["a b", "a/b", "..", "a;b", "a\nb"])
def test_backup_name_rejects_a_bad_label(run, label):
    result = run('backup_name "$LABEL"', LABEL=label)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "ERROR" in result.stderr


def test_manifest_compare_equal_and_different(run):
    base = "contests\t2\nusers\t131\nfsobjects\t900"
    # Equal, whatever the order.
    result = run('manifest_compare "$A" "$B"', A=base,
                 B="users\t131\nfsobjects\t900\ncontests\t2")
    assert result.returncode == 0
    assert result.stdout == ""
    # A changed count, a missing table and an extra table.
    result = run('manifest_compare "$A" "$B"', A=base,
                 B="contests\t2\nusers\t130\nextra\t5")
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        "extra: (absent) vs 5",
        "fsobjects: 900 vs (absent)",
        "users: 131 vs 130"]
    # An empty section is never "equal".
    result = run('manifest_compare "" ""')
    assert result.returncode != 0
    assert "ERROR" in result.stderr


def test_pg_tool_runs_in_the_compose_network_without_secrets_in_argv(
        run, tmp_path):
    log = tmp_path / "docker.log"
    result = run('parse_db_url "$URL" && pg_tool 17 psql -c "select 1"',
                 with_lib=True, CMS_PROJECT_NAME="proj",
                 FAKE_NETWORK="proj_default",
                 URL="postgresql://cms:s3cr%40t@db/cmsdb")
    assert result.returncode == 0, result.stderr
    network_ls, docker_run = log.read_text().splitlines()
    assert "label=com.docker.compose.project=proj" in network_ls
    assert "label=com.docker.compose.network=default" in network_ls
    assert re.fullmatch(
        r"run --rm --init --name cms-pgtool-\d+ --network proj_default "
        r"-e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE "
        r"postgres:17 psql -c select 1", docker_run)
    assert "s3cr" not in log.read_text()


def test_pg_tool_default_name_is_unchanged(run, tmp_path):
    # Under set -u, with the extra arguments unset or empty.
    for setup in ("", "PG_TOOL_ARGS=();"):
        log = tmp_path / "docker.log"
        log.unlink(missing_ok=True)
        result = run('set -u; %s echo "$$"; pg_tool 17 true' % setup,
                     with_lib=True, CMS_PROJECT_NAME="proj",
                     FAKE_NETWORK="proj_default")
        assert result.returncode == 0, result.stderr
        docker_run = log.read_text().splitlines()[1]
        assert docker_run == (
            "run --rm --init --name cms-pgtool-%s --network proj_default "
            "-e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE "
            "postgres:17 true" % result.stdout.strip())


def test_pg_tool_name_and_extra_args_can_be_overridden(run, tmp_path):
    log = tmp_path / "docker.log"
    # Two containers alive at once need two names; the dump needs a mount.
    result = run('parse_db_url "$URL"; PG_TOOL_NAME=cms-pgtool-dump; '
                 'PG_TOOL_ARGS=(-v "$BACKUPS:/backup" -i); '
                 "pg_tool 17 pg_dump -f /backup/x",
                 with_lib=True, CMS_PROJECT_NAME="proj",
                 FAKE_NETWORK="proj_default", BACKUPS="/srv/backups",
                 URL="postgresql://cms:s3cr%40t@db/cmsdb")
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[1] == (
        "run --rm --init --name cms-pgtool-dump --network proj_default "
        "-v /srv/backups:/backup -i "
        "-e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE "
        "postgres:17 pg_dump -f /backup/x")
    assert "s3cr" not in log.read_text()


def test_compose_network_fails_when_the_stack_was_never_started(run):
    result = run("pg_tool 17 psql", with_lib=True, CMS_PROJECT_NAME="proj")
    assert result.returncode != 0
    assert "run ./up.sh first" in result.stderr


def test_sourcing_the_library_has_no_side_effects(run, repo, tmp_path):
    before = sorted(os.listdir(repo))
    result = run("true")
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert sorted(os.listdir(repo)) == before
    # The fake docker logs only when it is called.
    assert not (tmp_path / "docker.log").exists()
