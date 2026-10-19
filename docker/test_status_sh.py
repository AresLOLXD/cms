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

"""Tests for status.sh, run with a fake docker on PATH."""

import os
import shutil
import subprocess
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# It answers status.sh's commands from FAKE_* variables, and logs each
# command line to FAKE_LOG when it is set.
FAKE_DOCKER = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [[ -n "${FAKE_LOG:-}" ]]; then echo "$*" >>"$FAKE_LOG"; fi
    case " $* " in
      *" supervisorctl "*)
        echo "cmslogservice   RUNNING   pid 10, uptime 1:00:00"
        exit "${FAKE_SUPERVISORCTL_RC:-0}" ;;
      *" cmsWatchdog "*)
        echo "${FAKE_WATCHDOG_LINE:-watchdog: observe}"
        exit "${FAKE_WATCHDOG_RC:-0}" ;;
      *" ps "*)
        echo "NAME   STATUS"
        exit 0 ;;
    esac
    exit 0
    """)


@pytest.fixture
def run_status(tmp_path):
    """Return a function that runs status.sh with the given FAKE_* env."""
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    shutil.copy(os.path.join(REPO, "status.sh"), repo / "status.sh")
    shutil.copy(os.path.join(REPO, "docker", "_lib.sh"),
                repo / "docker" / "_lib.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)

    def run(**env: str) -> subprocess.CompletedProcess:
        environment = {"PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
                       "HOME": str(tmp_path), **env}
        return subprocess.run(["bash", str(repo / "status.sh")],
                              env=environment, capture_output=True,
                              text=True, timeout=30)

    return run


@pytest.mark.parametrize("code", ["0", "3"])
def test_the_table_is_shown_when_supervisorctl_answers(run_status, code):
    # 3: some program is not RUNNING, a STOPPED one included.
    result = run_status(FAKE_SUPERVISORCTL_RC=code)
    assert result.returncode == 0
    assert "cmslogservice" in result.stdout
    assert "(container not running)" not in result.stdout
    assert "did not answer" not in result.stdout


def test_a_supervisord_that_does_not_answer_is_named(run_status):
    result = run_status(FAKE_SUPERVISORCTL_RC="124")
    assert result.returncode == 0
    assert "(supervisord did not answer in 10 s)" in result.stdout
    assert "(container not running)" not in result.stdout


@pytest.mark.parametrize("code", ["1", "125"])
def test_other_failures_mean_the_container_is_not_running(
        run_status, code):
    result = run_status(FAKE_SUPERVISORCTL_RC=code)
    assert result.returncode == 0
    assert "(container not running)" in result.stdout


def test_supervisorctl_runs_under_a_10_s_timeout(run_status, tmp_path):
    log = tmp_path / "docker.log"
    run_status(FAKE_LOG=str(log))
    commands = log.read_text().splitlines()
    # With spaces: pytest's tmp_path holds the test's name.
    status = [line for line in commands if " supervisorctl " in line]
    assert len(status) == 1
    assert "exec -T cms timeout 10 supervisorctl -c " in status[0]
    assert status[0].endswith(" status")


def test_the_watchdog_line_closes_the_status(run_status, tmp_path):
    log = tmp_path / "docker.log"
    result = run_status(FAKE_LOG=str(log), FAKE_WATCHDOG_LINE=(
        "watchdog: observe, last cycle 4 s ago, 0 open alerts"))
    assert result.stdout.rstrip().endswith(
        "watchdog: observe, last cycle 4 s ago, 0 open alerts")
    commands = log.read_text().splitlines()
    assert any("exec -T cms timeout 10 cmsWatchdog --status" in line
               for line in commands)


def test_a_watchdog_that_does_not_answer(run_status):
    result = run_status(FAKE_WATCHDOG_RC="1")
    assert result.returncode == 0
    assert result.stdout.rstrip().endswith(
        "(no answer from the cms container)")
