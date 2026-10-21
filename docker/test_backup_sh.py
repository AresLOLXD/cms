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

"""Tests for backup.sh, run with a fake docker and df on PATH."""

import os
import re
import shutil
import signal
import subprocess
import textwrap
import time
from typing import List, Optional

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASSWORD = "Sup3r@Secret/pw"
DB_URL = ("postgresql+psycopg2://cmsuser:Sup3r%40Secret%2Fpw"
          "@10.0.0.1:5432/cmsdb")
SNAPSHOT_ID = "00000003-0000001B-1"

# It answers backup.sh's docker commands from FAKE_* variables and logs
# each command line, and each line sent to the snapshot session, to
# FAKE_LOG. A "container" is the fake's own process: its pid goes to
# FAKE_DIR/<name>.pid, so "rm -f <name>" kills it as docker would.
FAKE_DOCKER = textwrap.dedent(r"""
    #!/usr/bin/env bash
    echo "$*" >>"$FAKE_LOG"
    case "$1" in
      network) echo "${FAKE_NETWORK-cms-prod_default}"; exit 0 ;;
      image)
        if [[ -e "$FAKE_DIR/pulled-$3" ]]; then exit 0; fi
        exit "${FAKE_IMAGE_MISSING:-0}" ;;
      pull)
        if [[ "${FAKE_PULL_RC:-0}" != 0 ]]; then exit "$FAKE_PULL_RC"; fi
        touch "$FAKE_DIR/pulled-$2"
        exit 0 ;;
      rm)
        shift 2
        if [[ -n "${FAKE_RM_SLEEP:-}" ]]; then sleep "$FAKE_RM_SLEEP"; fi
        for name in "$@"; do
          if [[ -f "$FAKE_DIR/$name.pid" ]]; then
            kill "$(cat "$FAKE_DIR/$name.pid")" 2>/dev/null
          fi
          rm -f "$FAKE_DIR/$name.created"
        done
        exit 0 ;;
      run) ;;
      *) exit 0 ;;
    esac
    name="" mount=""
    while (( $# )) && [[ "$1" != postgres:* ]]; do
      case "$1" in
        --name) name="$2"; shift ;;
        -v) mount="${2%%:*}"; shift ;;
      esac
      shift
    done
    shift
    # The dump container is still being created: "rm -f" cannot reach it.
    if [[ "$1" == nice && -n "${FAKE_DUMP_START_DELAY:-}" ]]; then
      sleep "$FAKE_DUMP_START_DELAY"
    fi
    # Ctrl+C while the daemon creates the container: the client ends, and
    # the daemon still creates it just after, left Created ($name.created).
    if [[ "$1" == nice && -n "${FAKE_LATE_CREATE:-}" ]]; then
      ( sleep 1.5; touch "$FAKE_DIR/$name.created" ) >/dev/null 2>&1 &
      sleep 1
      exit 130
    fi
    echo $$ >"$FAKE_DIR/$name.pid"
    case "$1" in
      psql)
        if [[ " $* " == *" -c "* ]]; then
          printf '%s\t%s\n' "${FAKE_VERSION_NUM:-160015}" \
            "${FAKE_DB_SIZE:-1000}"
          exit 0
        fi
        # The snapshot session: answer each line as it comes.
        while IFS= read -r line; do
          echo "session: $line" >>"$FAKE_LOG"
          case "$line" in
            *"'end of reply'"*)
              echo "end of reply"
              if [[ -n "${FAKE_SESSION_DIES:-}" ]]; then exit 2; fi ;;
            *pg_export_snapshot*)
              printf 'snapshot\t00000003-0000001B-1\n' ;;
            *pg_largeobject_metadata*) printf 'large_objects\t3\n' ;;
            *gexec*) printf 'table\tcontests\t2\ntable\tusers\t5\n' ;;
            "SELECT '"*)
              key="${line#"SELECT '"}"
              printf '%s\tfake\n' "${key%%"'"*}" ;;
          esac
        done
        echo "session: closed" >>"$FAKE_LOG"
        exit 0 ;;
      nice)
        last="${!#}"
        dir="$mount${last#/backups}"
        mkdir "$dir"
        echo toc >"$dir/toc.dat"
        # The container's umask is 022, not the script's 077.
        chmod 644 "$dir/toc.dat"
        echo rows >"$dir/3001.dat.zst"
        for (( oid = 16401; oid < 16401 + ${FAKE_BLOBS:-3}; oid++ )); do
          echo blob >"$dir/blob_$oid.dat.zst"
          echo "$oid blob_$oid.dat.zst"
        done >"$dir/blobs_3003.toc"
        if [[ -n "${FAKE_DUMP_SLEEP:-}" ]]; then
          exec sleep "$FAKE_DUMP_SLEEP"
        fi
        exit "${FAKE_DUMP_RC:-0}" ;;
      pg_restore)
        echo ";"
        echo "; Archive created at 2026-10-20 12:00:00 UTC"
        for table in contests users; do
          echo "215; 1259 16390 TABLE public $table cmsuser"
          if [[ "$table" != "${FAKE_TOC_SKIP:-}" ]]; then
            echo "3001; 0 16390 TABLE DATA public $table cmsuser"
          fi
        done
        echo "3003; 0 0 BLOBS - LARGE OBJECTS 16401..16403 cmsuser"
        exit 0 ;;
    esac
    exit 0
    """).lstrip()

FAKE_DF = textwrap.dedent("""\
    #!/usr/bin/env bash
    echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
    echo "/dev/fake 900000000 1 ${FAKE_DF_AVAIL_KB:-500000000} 1% /"
    """)

# The tests run as a normal user; backup.sh needs root.
FAKE_ID = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [[ "$1" == -u ]]; then echo "${FAKE_UID:-0}"; else echo operator; fi
    """)


@pytest.fixture
def repo(tmp_path):
    """A copy of the files backup.sh needs, without .env."""
    (tmp_path / "repo" / "docker").mkdir(parents=True)
    for name in ("backup.sh", "pyproject.toml", "docker/_lib.sh",
                 "docker/_backup_lib.sh"):
        shutil.copy(os.path.join(REPO, name), tmp_path / "repo" / name)
    return tmp_path / "repo"


@pytest.fixture
def backup(repo, tmp_path):
    """Return a function that starts backup.sh with the given FAKE_* env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "fake").mkdir()
    for name, text in (("docker", FAKE_DOCKER), ("df", FAKE_DF),
                       ("id", FAKE_ID)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)

    def start(*args: str, **env: str) -> subprocess.Popen:
        environment = {"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                       "HOME": str(tmp_path),
                       "FAKE_LOG": str(tmp_path / "docker.log"),
                       "FAKE_DIR": str(tmp_path / "fake"),
                       "CMS_PROJECT_NAME": "cms-prod",
                       "CMS_USE_LOCALDB": "false",
                       "CMS_DB_URL": DB_URL, **env}
        # A session of its own, so a test can Ctrl+C the whole group.
        return subprocess.Popen(
            ["bash", str(repo / "backup.sh"), *args], cwd=repo,
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)

    return start


def finish(process: subprocess.Popen, answer: Optional[str] = "y\n"
           ) -> subprocess.CompletedProcess:
    """Answer the confirmation and wait for the script to end."""
    out, err = process.communicate(answer, timeout=60)
    return subprocess.CompletedProcess(process.args, process.returncode,
                                       out, err)


def log_lines(tmp_path) -> List[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def docker_runs(tmp_path) -> List[str]:
    return [line for line in log_lines(tmp_path) if line.startswith("run ")]


def dump_line(tmp_path) -> str:
    (line,) = [line for line in docker_runs(tmp_path) if " pg_dump " in line]
    return line


def assert_nothing_left(repo, tmp_path,
                        result: subprocess.CompletedProcess) -> None:
    """No backup, no .partial, and both containers were removed, with
    the anonymous volume of the postgres image (-v)."""
    assert result.returncode != 0
    assert list((repo / "backups").iterdir()) == []
    (session,) = [line for line in docker_runs(tmp_path) if " -i " in line]
    pid = re.search(r"--name cms-pgtool-(\d+)-snapshot ", session).group(1)
    removals = [set(line.split()[2:]) for line in log_lines(tmp_path)
                if line.startswith("rm -f ")]
    containers = {"-v", "cms-pgtool-%s" % pid, "cms-pgtool-%s-snapshot" % pid}
    assert any(containers <= removed for removed in removals)


@pytest.mark.parametrize("label", ["a b", "a/b", "..", "../x", "a;b"])
def test_a_bad_label_is_refused_before_anything_runs(
        backup, repo, tmp_path, label):
    result = finish(backup(label))
    assert result.returncode != 0
    assert "ERROR" in result.stderr
    assert log_lines(tmp_path) == []
    assert not (repo / "backups").exists()


def test_the_image_is_pulled_first_and_a_failed_pull_stops(
        backup, repo, tmp_path):
    # A server 18 needs postgres:17 for the version query, then postgres:18.
    result = finish(backup(FAKE_IMAGE_MISSING="1", FAKE_VERSION_NUM="180001"))
    assert result.returncode == 0, result.stderr
    lines = log_lines(tmp_path)
    for major in ("17", "18"):
        first_run = min(i for i, line in enumerate(lines)
                        if line.startswith("run ")
                        and " postgres:%s " % major in line)
        assert lines.index("pull postgres:%s" % major) < first_run
    (version_query,) = [line for line in docker_runs(tmp_path)
                        if " postgres:17 " in line]
    assert " psql " in version_query and " -c " in version_query
    assert " postgres:18 nice -n 10 pg_dump " in dump_line(tmp_path)

    shutil.rmtree(repo / "backups")
    (tmp_path / "docker.log").unlink()
    (tmp_path / "fake2").mkdir()
    result = finish(backup(FAKE_IMAGE_MISSING="1", FAKE_PULL_RC="1",
                           FAKE_DIR=str(tmp_path / "fake2")))
    assert result.returncode != 0
    assert "could not pull postgres:17" in result.stderr
    assert "pull postgres:17" in log_lines(tmp_path)
    assert docker_runs(tmp_path) == []
    assert not (repo / "backups").exists()


def test_not_enough_free_space_stops_before_writing(backup, repo, tmp_path):
    result = finish(backup(FAKE_DB_SIZE=str(10 * 2**30),
                           FAKE_DF_AVAIL_KB="1000"))
    assert result.returncode != 0
    assert "not enough free space" in result.stderr
    assert "Start the backup?" not in result.stdout
    assert list((repo / "backups").iterdir()) == []
    assert all(" -i " not in line and " pg_dump " not in line
               for line in docker_runs(tmp_path))


def test_answering_no_writes_nothing(backup, repo, tmp_path):
    result = finish(backup(), answer="n\n")
    assert result.returncode != 0
    assert list((repo / "backups").iterdir()) == []
    assert all(" -i " not in line and " pg_dump " not in line
               for line in docker_runs(tmp_path))


def test_a_failing_pg_dump_leaves_nothing_behind(backup, repo, tmp_path):
    result = finish(backup(FAKE_DUMP_RC="1"))
    assert_nothing_left(repo, tmp_path, result)
    assert "pg_dump failed" in result.stderr


def test_ctrl_c_cleans_up(backup, repo, tmp_path):
    process = backup(FAKE_DUMP_SLEEP="30")
    process.stdin.write("y\n")
    process.stdin.flush()
    wait_for(lambda: list((repo / "backups").glob("*.partial")),
             "the dump never started")
    os.killpg(process.pid, signal.SIGINT)
    # Well before the 30 s sleep of the fake pg_dump ends.
    result = finish(process, answer=None)
    assert result.returncode == 130
    assert_nothing_left(repo, tmp_path, result)


def test_sighup_cleans_up(backup, repo, tmp_path):
    process = backup(FAKE_DUMP_SLEEP="30")
    process.stdin.write("y\n")
    process.stdin.flush()
    wait_for(lambda: list((repo / "backups").glob("*.partial")),
             "the dump never started")
    os.killpg(process.pid, signal.SIGHUP)
    result = finish(process, answer=None)
    assert result.returncode == 129
    assert_nothing_left(repo, tmp_path, result)


def wait_for(condition, what: str) -> None:
    deadline = time.monotonic() + 20
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_second_ctrl_c_does_not_stop_the_cleanup(backup, repo, tmp_path):
    process = backup(FAKE_DUMP_SLEEP="10", FAKE_RM_SLEEP="2")
    process.stdin.write("y\n")
    process.stdin.flush()
    wait_for(lambda: list((repo / "backups").glob("*.partial")),
             "the dump never started")
    os.killpg(process.pid, signal.SIGINT)
    wait_for(lambda: any(line.startswith("rm -f ")
                         for line in log_lines(tmp_path)),
             "the cleanup never started")
    os.killpg(process.pid, signal.SIGINT)
    result = finish(process, answer=None)
    assert result.returncode == 130
    assert_nothing_left(repo, tmp_path, result)


def test_ctrl_c_while_the_dump_container_starts_leaves_nothing(
        backup, repo, tmp_path):
    # "rm -f" finds no container yet; pg_dump creates .partial after it.
    process = backup(FAKE_DUMP_START_DELAY="1")
    process.stdin.write("y\n")
    process.stdin.flush()
    wait_for(lambda: any(" pg_dump " in line
                         for line in log_lines(tmp_path)),
             "the dump never started")
    os.killpg(process.pid, signal.SIGINT)
    result = finish(process, answer=None)
    assert result.returncode == 130
    assert list((repo / "backups").iterdir()) == []


def test_a_container_created_just_after_a_ctrl_c_is_removed(
        backup, repo, tmp_path):
    # Seen with Docker 29: the container appears up to 0.6 s after the
    # docker run it belongs to has ended, Created, and --rm never removes it.
    process = backup(FAKE_LATE_CREATE="1")
    process.stdin.write("y\n")
    process.stdin.flush()
    wait_for(lambda: any(" pg_dump " in line
                         for line in log_lines(tmp_path)),
             "the dump never started")
    os.killpg(process.pid, signal.SIGINT)
    result = finish(process, answer=None)
    assert result.returncode == 130
    time.sleep(1)  # past the late create of the fake daemon
    assert list((tmp_path / "fake").glob("*.created")) == []
    assert list((repo / "backups").iterdir()) == []


def test_it_refuses_to_run_without_root(backup, repo, tmp_path):
    result = finish(backup("pre", FAKE_UID="1"))
    assert result.returncode != 0
    assert "run it as root: sudo ./backup.sh" in result.stderr
    assert log_lines(tmp_path) == []
    assert not (repo / "backups").exists()


def test_no_file_of_the_backup_is_readable_by_others(backup, repo):
    result = finish(backup())
    assert result.returncode == 0, result.stderr
    (backup_dir,) = (repo / "backups").iterdir()
    paths = [backup_dir, *backup_dir.iterdir()]
    assert "toc.dat" in [path.name for path in paths]
    for path in paths:
        assert path.stat().st_mode & 0o077 == 0, path


def test_the_session_turns_the_server_timeouts_off(backup, tmp_path):
    result = finish(backup())
    assert result.returncode == 0, result.stderr
    session = [line for line in log_lines(tmp_path)
               if line.startswith("session: ")]
    begin = session.index(
        "session: BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;")
    for setting in ("statement_timeout", "lock_timeout",
                    "idle_in_transaction_session_timeout"):
        assert "session: SET %s = 0;" % setting in session[:begin]


def test_exactly_enough_free_space_passes(backup, repo):
    # 1000 KiB free is 1024000 bytes.
    result = finish(backup(FAKE_DF_AVAIL_KB="1000", FAKE_DB_SIZE="1024000"))
    assert result.returncode == 0, result.stderr
    result = finish(backup(FAKE_DF_AVAIL_KB="1000", FAKE_DB_SIZE="1024001"))
    assert result.returncode != 0
    assert "not enough free space" in result.stderr


def test_a_failed_run_leaves_an_existing_finished_backup_untouched(
        backup, repo):
    finished = repo / "backups" / "cms-20260101-000000-old"
    finished.mkdir(parents=True)
    (finished / "MANIFEST").write_text("table\tusers\t5\n")
    result = finish(backup(FAKE_DUMP_RC="1"))
    assert result.returncode != 0
    assert [path.name for path in (repo / "backups").iterdir()] == [
        finished.name]
    assert [path.name for path in finished.iterdir()] == ["MANIFEST"]
    assert (finished / "MANIFEST").read_text() == "table\tusers\t5\n"


def test_a_snapshot_session_that_ends_during_the_dump_fails(
        backup, repo, tmp_path):
    result = finish(backup(FAKE_SESSION_DIES="1"))
    assert_nothing_left(repo, tmp_path, result)
    assert "the snapshot session ended during the dump" in result.stderr


@pytest.mark.parametrize("env", [{"FAKE_TOC_SKIP": "users"},
                                 {"FAKE_BLOBS": "2"}])
def test_a_dump_that_does_not_match_the_manifest_fails(
        backup, repo, tmp_path, env):
    result = finish(backup(**env))
    assert_nothing_left(repo, tmp_path, result)
    assert "ERROR" in result.stderr


def test_the_password_never_appears_in_any_command_line(
        backup, repo, tmp_path):
    result = finish(backup("pre"))
    assert result.returncode == 0, result.stderr
    (backup_dir,) = (repo / "backups").iterdir()
    texts = [(tmp_path / "docker.log").read_text(), result.stdout,
             result.stderr, (backup_dir / "MANIFEST").read_text()]
    for text in texts:
        assert "Sup3r" not in text
        assert "Secret" not in text
    assert all(" -e PGPASSWORD " in line for line in docker_runs(tmp_path))


def test_a_complete_run_writes_manifest_checksums_and_renames(
        backup, repo, tmp_path):
    result = finish(backup("pre"))
    assert result.returncode == 0, result.stderr
    backups = repo / "backups"
    assert backups.stat().st_mode & 0o777 == 0o700
    (backup_dir,) = backups.iterdir()
    name = backup_dir.name
    assert re.fullmatch(r"cms-\d{8}-\d{6}-pre", name)
    assert (backup_dir / "MANIFEST").stat().st_mode & 0o777 == 0o600
    check = subprocess.run(["sha256sum", "--strict", "-c", "SHA256SUMS"],
                           cwd=backup_dir, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    # Every file but SHA256SUMS itself, MANIFEST included.
    listed = {line.split("  ", 1)[1] for line in
              (backup_dir / "SHA256SUMS").read_text().splitlines()}
    assert listed == {path.name for path in backup_dir.iterdir()
                      if path.name != "SHA256SUMS"}
    assert {"MANIFEST", "blob_16401.dat.zst"} <= listed

    manifest = (backup_dir / "MANIFEST").read_text().splitlines()
    keys = [line.split("\t")[0] for line in manifest]
    for key in ("created_utc", "git_commit", "fork_version",
                "server_version", "pg_dump_version", "database", "encoding",
                "lc_collate", "lc_ctype", "large_objects"):
        assert key in keys
    assert "snapshot" not in keys
    assert "large_objects\t3" in manifest
    assert "table\tcontests\t2" in manifest
    assert "table\tusers\t5" in manifest

    # The session takes the snapshot first, in REPEATABLE READ.
    session = [line for line in log_lines(tmp_path)
               if line.startswith("session: ")]
    begin = next(i for i, line in enumerate(session)
                 if line.startswith("session: BEGIN "))
    assert session[begin].startswith(
        "session: BEGIN ISOLATION LEVEL REPEATABLE READ")
    assert "pg_export_snapshot()" in session[begin + 1]
    # The session ends after the dump and before the verification.
    lines = log_lines(tmp_path)
    assert (lines.index(dump_line(tmp_path))
            < lines.index("session: closed")
            < min(i for i, line in enumerate(lines)
                  if " pg_restore -l " in line))
    (session_run,) = [line for line in docker_runs(tmp_path)
                      if " -i " in line]
    pid = re.search(r"--name cms-pgtool-(\d+)-snapshot ",
                    session_run).group(1)
    assert session_run.endswith(" psql -X -q -At -F \t -v ON_ERROR_STOP=1")
    assert dump_line(tmp_path) == (
        "run --rm --init --name cms-pgtool-%s --network cms-prod_default "
        "-v %s:/backups "
        "-e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE "
        "postgres:17 nice -n 10 pg_dump --snapshot=%s -Fd -j 4 "
        "--compress=zstd:3 -f /backups/%s.partial"
        % (pid, backups, SNAPSHOT_ID, name))

    assert str(backup_dir) in result.stdout
    assert "rsync -a" in result.stdout
    assert ".env" in result.stdout


@pytest.mark.parametrize("args, workers", [
    ((), "4"), (("-j", "8"), "8"), (("pre", "-j", "2"), "2"),
    (("-j", "16", "pre"), "16")])
def test_default_workers_is_four_and_j_overrides(
        backup, tmp_path, args, workers):
    result = finish(backup(*args))
    assert result.returncode == 0, result.stderr
    assert " -j %s --compress=zstd:3 " % workers in dump_line(tmp_path)


@pytest.mark.parametrize("args", [("-j",), ("-j", "0"), ("-j", "x"),
                                  ("a", "b"), ("--force",)])
def test_bad_arguments_are_refused_before_anything_runs(
        backup, repo, tmp_path, args):
    result = finish(backup(*args))
    assert result.returncode != 0
    assert log_lines(tmp_path) == []
    assert not (repo / "backups").exists()
