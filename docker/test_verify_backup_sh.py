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

"""Tests for verify-backup.sh, run with a fake docker on PATH.

The backup directories are real files with real checksums; only docker is
fake.
"""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import textwrap
import time
from pathlib import Path
from typing import List

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# As backup.sh writes it, plus a key it does not know.
MANIFEST = "".join(line + "\n" for line in [
    "created_utc\t2026-10-20T12:00:00Z",
    "git_commit\tabc123",
    "fork_version\t1.6.dev0+omi.8",
    "server_version\t16.4 (Debian 16.4-1.pgdg120+1)",
    "pg_dump_version\t17.6",
    "database\tcmsdb",
    "encoding\tUTF8",
    "lc_collate\ten_US.utf8",
    "lc_ctype\ten_US.utf8",
    "database_setting\t*\tsearch_path=public",
    "large_objects\t3",
    "table\tcontests\t2",
    "table\tusers\t5",
    "future_key\tignored",
])

# It answers the docker commands of verify-backup.sh from FAKE_* variables
# and logs each command line to FAKE_LOG. A "container" is the file
# FAKE_DIR/<name>.container, which "rm" deletes; a running pg_restore is
# the fake's own process, whose pid goes to FAKE_DIR/<name>.exec.pid, so
# "rm -f" kills it as docker would. The password the container is given
# goes to FAKE_DIR/password.
FAKE_DOCKER = textwrap.dedent(r"""
    #!/usr/bin/env bash
    echo "$*" >>"$FAKE_LOG"
    case "$1" in
      run)
        shift
        while (( $# )) && [[ "$1" != --name ]]; do shift; done
        printf '%s' "${POSTGRES_PASSWORD-}" >"$FAKE_DIR/password"
        # Ctrl+C while the daemon creates the container: the client ends,
        # and the daemon still creates it just after, left Created.
        if [[ -n "${FAKE_LATE_CREATE:-}" ]]; then
          ( sleep 0.5; touch "$FAKE_DIR/$2.container" ) >/dev/null 2>&1 &
          sleep 30
          exit 130
        fi
        touch "$FAKE_DIR/$2.container"
        echo 3f2a9c0d41b7
        exit 0 ;;
      rm)
        shift
        if [[ -n "${FAKE_RM_SLEEP:-}" ]]; then sleep "$FAKE_RM_SLEEP"; fi
        for name in "$@"; do
          if [[ "$name" == -* ]]; then continue; fi
          if [[ -f "$FAKE_DIR/$name.exec.pid" ]]; then
            kill "$(cat "$FAKE_DIR/$name.exec.pid")" 2>/dev/null
          fi
          rm -f "$FAKE_DIR/$name.container"
        done
        exit 0 ;;
      exec) ;;
      *) exit 0 ;;
    esac
    shift
    if [[ "$1" == -u ]]; then shift 2; fi
    name="$1"
    shift
    if [[ ! -f "$FAKE_DIR/$name.container" ]]; then
      echo "Error: No such container: $name" >&2
      exit 1
    fi
    case "$1" in
      pg_isready)
        count=$(( $(cat "$FAKE_DIR/isready" 2>/dev/null || echo 0) + 1 ))
        echo "$count" >"$FAKE_DIR/isready"
        (( count > ${FAKE_READY_AFTER:-0} )) ;;
      pg_restore)
        echo $$ >"$FAKE_DIR/$name.exec.pid"
        if [[ -n "${FAKE_RESTORE_SLEEP:-}" ]]; then
          exec sleep "$FAKE_RESTORE_SLEEP"
        fi
        exit "${FAKE_RESTORE_RC:-0}" ;;
      psql)
        while [[ "$1" != -c ]]; do shift; done
        case "$2" in
          *"CREATE EXTENSION"*) ;;
          *"digest("*) echo "${FAKE_BAD_DIGESTS:-0}" ;;
          *"NOT EXISTS"*) echo "${FAKE_ORPHANS:-0}" ;;
          *"IS NULL"*) echo "${FAKE_MISSING:-0}" ;;
          *query_to_xml*)
            printf 'contests\t%s\nusers\t%s\n(large objects)\t%s\n' \
              "${FAKE_CONTESTS:-2}" "${FAKE_USERS:-5}" "${FAKE_BLOBS:-3}" ;;
          *) echo "unexpected query: $2" >&2; exit 1 ;;
        esac ;;
    esac
    """).lstrip()


def make_backup(path: Path, manifest: str = MANIFEST) -> Path:
    """Write a backup directory with real checksums, as backup.sh does."""
    path.mkdir(parents=True, exist_ok=True)
    files = {"toc.dat": "toc", "3001.dat.zst": "rows",
             "blobs_3003.toc": "16401 blob_16401.dat.zst\n",
             "blob_16401.dat.zst": "blob", "MANIFEST": manifest}
    for name, text in files.items():
        (path / name).write_text(text)
    (path / "SHA256SUMS").write_text("".join(
        "%s  %s\n" % (hashlib.sha256(text.encode()).hexdigest(), name)
        for name, text in files.items()))
    return path


@pytest.fixture
def backup_dir(tmp_path) -> Path:
    return make_backup(tmp_path / "backups" / "cms-20261020-120000-pre")


@pytest.fixture
def repo(tmp_path):
    """A copy of the files verify-backup.sh needs, without .env."""
    (tmp_path / "repo" / "docker").mkdir(parents=True)
    for name in ("verify-backup.sh", "docker/_backup_lib.sh"):
        shutil.copy(os.path.join(REPO, name), tmp_path / "repo" / name)
    return tmp_path / "repo"


@pytest.fixture
def verify(repo, tmp_path):
    """Return a function that starts verify-backup.sh with FAKE_* env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "fake").mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)

    def start(*args: str, **env: str) -> subprocess.Popen:
        environment = {"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                       "HOME": str(tmp_path),
                       "FAKE_LOG": str(tmp_path / "docker.log"),
                       "FAKE_DIR": str(tmp_path / "fake"), **env}
        # A session of its own, so a test can Ctrl+C the whole group.
        return subprocess.Popen(
            ["bash", str(repo / "verify-backup.sh"), *args], cwd=repo,
            env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)

    return start


def finish(process: subprocess.Popen,
           timeout: int = 60) -> subprocess.CompletedProcess:
    """Wait for the script to end."""
    out, err = process.communicate(timeout=timeout)
    return subprocess.CompletedProcess(process.args, process.returncode,
                                       out, err)


def log_lines(tmp_path) -> List[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def container_name(tmp_path) -> str:
    (run,) = [line for line in log_lines(tmp_path)
              if line.startswith("run ")]
    return re.search(r"--name (cms-verify-\d+) ", run).group(1)


def assert_container_removed(tmp_path) -> None:
    """The container went away with its anonymous volume (rm -v)."""
    name = container_name(tmp_path)
    assert "rm -f -v %s" % name in log_lines(tmp_path)
    assert not (tmp_path / "fake" / (name + ".container")).exists()


def wait_for(condition, what: str) -> None:
    deadline = time.monotonic() + 20
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_missing_argument_or_directory_is_refused(
        verify, backup_dir, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    for args in [(), (str(backup_dir), "extra"), (str(tmp_path / "nope"),),
                 (str(backup_dir / "MANIFEST"),), (str(empty),)]:
        result = finish(verify(*args))
        assert result.returncode != 0, args
        assert "ERROR" in result.stderr or "Usage" in result.stderr, args
    assert log_lines(tmp_path) == []


def test_a_corrupted_file_fails_the_checksum_and_names_it(
        verify, backup_dir, tmp_path):
    (backup_dir / "3001.dat.zst").write_text("rowz")
    result = finish(verify(str(backup_dir)))
    assert result.returncode != 0
    assert "3001.dat.zst: FAILED" in result.stderr
    assert "checksum" in result.stderr
    assert log_lines(tmp_path) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_stops_with_a_hint_to_use_sudo(
        verify, backup_dir, tmp_path):
    (backup_dir / "3001.dat.zst").chmod(0)
    try:
        result = finish(verify(str(backup_dir)))
    finally:
        (backup_dir / "3001.dat.zst").chmod(0o644)
    assert result.returncode != 0
    assert "3001.dat.zst" in result.stderr
    assert "run it with sudo, or as the owner of the files" in result.stderr
    assert log_lines(tmp_path) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root enters any directory")
def test_a_backup_in_a_closed_directory_gets_a_hint_to_use_sudo(
        verify, backup_dir, tmp_path):
    # backups/ has mode 700 and belongs to root.
    backup_dir.parent.chmod(0)
    try:
        result = finish(verify(str(backup_dir)))
    finally:
        backup_dir.parent.chmod(0o700)
    assert result.returncode != 0
    assert "run it with sudo" in result.stderr
    assert log_lines(tmp_path) == []


@pytest.mark.parametrize("suffix", ["", "/"])
def test_a_partial_directory_is_refused(verify, backup_dir, tmp_path, suffix):
    partial = backup_dir.rename(backup_dir.with_name(
        backup_dir.name + ".partial"))
    result = finish(verify(str(partial) + suffix))
    assert result.returncode != 0
    assert ".partial" in result.stderr
    assert log_lines(tmp_path) == []


@pytest.mark.parametrize("env, difference", [
    ({"FAKE_USERS": "4"}, "users: 5 vs 4"),
    ({"FAKE_CONTESTS": "3"}, "contests: 2 vs 3"),
    ({"FAKE_BLOBS": "2"}, "(large objects): 3 vs 2")])
def test_count_mismatch_fails_and_names_the_table(
        verify, backup_dir, tmp_path, env, difference):
    result = finish(verify(str(backup_dir), **env))
    assert result.returncode != 0
    assert difference in result.stderr
    assert_container_removed(tmp_path)


def test_a_missing_large_object_fails(verify, backup_dir, tmp_path):
    result = finish(verify(str(backup_dir), FAKE_MISSING="2"))
    assert result.returncode != 0
    assert "fsobjects.loid: 2 rows" in result.stderr


def test_a_wrong_digest_fails_the_sha1_check(verify, backup_dir, tmp_path):
    result = finish(verify(str(backup_dir), FAKE_BAD_DIGESTS="1"))
    assert result.returncode != 0
    assert "SHA1 of the large objects: 1 rows" in result.stderr


def test_orphan_large_objects_are_informational_only(
        verify, backup_dir, tmp_path):
    result = finish(verify(str(backup_dir), FAKE_ORPHANS="4"))
    assert result.returncode == 0, result.stderr
    assert "4 orphan large objects" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("env", [{"FAKE_BAD_DIGESTS": "1"},
                                 {"FAKE_USERS": "4"},
                                 {"FAKE_RESTORE_RC": "1"}])
def test_the_throwaway_container_and_volume_are_removed_on_failure(
        verify, backup_dir, tmp_path, env):
    result = finish(verify(str(backup_dir), **env))
    assert result.returncode != 0
    assert_container_removed(tmp_path)


def test_a_failing_pg_restore_stops_before_the_checks(
        verify, backup_dir, tmp_path):
    result = finish(verify(str(backup_dir), FAKE_RESTORE_RC="1"))
    assert result.returncode != 0
    assert "pg_restore failed" in result.stderr
    assert not any(" psql " in line for line in log_lines(tmp_path))


def test_the_throwaway_container_and_volume_are_removed_on_sigint(
        verify, backup_dir, tmp_path):
    process = verify(str(backup_dir), FAKE_RESTORE_SLEEP="30")
    wait_for(lambda: any(" pg_restore " in line
                         for line in log_lines(tmp_path)),
             "the restore never started")
    os.killpg(process.pid, signal.SIGINT)
    # Well before the 30 s sleep of the fake pg_restore ends.
    result = finish(process, timeout=10)
    assert result.returncode == 130
    assert_container_removed(tmp_path)


def test_sigterm_to_the_script_alone_is_handled_at_once(
        verify, backup_dir, tmp_path):
    # Only bash gets it: the trap must not wait for pg_restore to end.
    process = verify(str(backup_dir), FAKE_RESTORE_SLEEP="30")
    wait_for(lambda: any(" pg_restore " in line
                         for line in log_lines(tmp_path)),
             "the restore never started")
    process.send_signal(signal.SIGTERM)
    result = finish(process, timeout=10)
    assert result.returncode == 143
    assert_container_removed(tmp_path)


def test_a_container_created_just_after_a_ctrl_c_is_removed(
        verify, backup_dir, tmp_path):
    # Seen with Docker 29: the container appears up to 0.6 s after the
    # docker run it belongs to has ended, Created, with its volume.
    process = verify(str(backup_dir), FAKE_LATE_CREATE="1")
    wait_for(lambda: (tmp_path / "fake" / "password").exists(),
             "docker run never started")
    os.killpg(process.pid, signal.SIGINT)
    result = finish(process, timeout=10)
    assert result.returncode == 130
    time.sleep(1)  # past the late create of the fake daemon
    assert list((tmp_path / "fake").glob("*.container")) == []


def test_a_second_ctrl_c_does_not_stop_the_cleanup(
        verify, backup_dir, tmp_path):
    process = verify(str(backup_dir), FAKE_RESTORE_SLEEP="30",
                     FAKE_RM_SLEEP="2")
    wait_for(lambda: any(" pg_restore " in line
                         for line in log_lines(tmp_path)),
             "the restore never started")
    os.killpg(process.pid, signal.SIGINT)
    wait_for(lambda: any(line.startswith("rm -f ")
                         for line in log_lines(tmp_path)),
             "the cleanup never started")
    os.killpg(process.pid, signal.SIGINT)
    result = finish(process, timeout=10)
    assert result.returncode == 130
    assert_container_removed(tmp_path)


def test_it_waits_for_the_final_server_on_tcp(verify, backup_dir, tmp_path):
    # The temporary server of the image's init only listens on its socket.
    result = finish(verify(str(backup_dir), FAKE_READY_AFTER="2"))
    assert result.returncode == 0, result.stderr
    ready = [i for i, line in enumerate(log_lines(tmp_path))
             if " pg_isready " in line]
    assert len(ready) == 3
    assert all(" pg_isready -q -h 127.0.0.1 -U postgres" in
               log_lines(tmp_path)[i] for i in ready)
    restore = next(i for i, line in enumerate(log_lines(tmp_path))
                   if " pg_restore " in line)
    assert ready[-1] < restore


def test_the_image_major_is_the_one_of_the_dump(verify, tmp_path):
    backup = make_backup(tmp_path / "other", MANIFEST.replace(
        "pg_dump_version\t17.6", "pg_dump_version\t18.1"))
    result = finish(verify(str(backup)))
    assert result.returncode == 0, result.stderr
    (run,) = [line for line in log_lines(tmp_path)
              if line.startswith("run ")]
    assert " postgres:18 " in run


def test_a_clean_backup_exits_zero(verify, backup_dir, tmp_path):
    before = sorted(path.name for path in backup_dir.iterdir())
    result = finish(verify(str(backup_dir)))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "OK" in result.stdout
    assert sorted(path.name for path in backup_dir.iterdir()) == before

    lines = log_lines(tmp_path)
    name = container_name(tmp_path)
    jobs = subprocess.check_output(["nproc"], text=True).strip()
    # No network, the backup read-only, the password by name only.
    assert lines[0] == (
        "run -d --name %s --network none -v %s:/backup:ro,z "
        "-e POSTGRES_PASSWORD postgres:17 -c fsync=off"
        % (name, backup_dir))
    restores = [line for line in lines if " pg_restore " in line]
    assert restores == [
        "exec -u root %s pg_restore -U postgres -d postgres -j %s "
        "--no-owner --no-acl --exit-on-error /backup" % (name, jobs)]
    assert_container_removed(tmp_path)

    # A random password per run, and nowhere but the container's env.
    password = (tmp_path / "fake" / "password").read_text()
    assert len(password) >= 24
    for text in ["\n".join(lines), result.stdout, result.stderr]:
        assert password not in text
    shutil.rmtree(tmp_path / "fake")
    (tmp_path / "fake").mkdir()
    (tmp_path / "docker.log").unlink()
    assert finish(verify(str(backup_dir))).returncode == 0
    assert (tmp_path / "fake" / "password").read_text() != password
