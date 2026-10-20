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

"""Pin cmsWatchdog's thresholds against the timeouts of CMS (#38).

Each trip must come after the timeout that heals the same failure by
itself: a watchdog that restarts first would restart programs about to
recover. Moving a rung of this ladder fails a test that names
cms/watchdog/policy.py.

"""

import unittest

from cms.db import POOL_TIMEOUT_S
from cms.db.connect_options import LIBPQ_DEFAULTS
from cms.io.async_rpc import FIRE_AND_FORGET_TIMEOUT
from cms.service.EvaluationService import EvaluationService
from cms.service.ProxyService import CONNECT_TIMEOUT, READ_TIMEOUT, \
    ProxyExecutor
from cms.watchdog.policy import CYCLE_S, HTTP_TIMEOUT_S, MISSES_TO_TRIP, \
    RPC_TIMEOUT_S, SECTION_TRIP_S, WORKER_DISABLED_TRIP_S


WATCHDOG = "cmsWatchdog (cms/watchdog/policy.py)"


class TestLadder(unittest.TestCase):

    def test_the_earliest_liveness_trip_beats_the_39_ladder(self):
        # A pooled session waits POOL_TIMEOUT_S for a connection, and a
        # dead connection fails after tcp_user_timeout (milliseconds).
        ladder = max(POOL_TIMEOUT_S,
                     int(LIBPQ_DEFAULTS["tcp_user_timeout"]) / 1000)
        for name, timeout in (("R1", HTTP_TIMEOUT_S),
                              ("R2", RPC_TIMEOUT_S)):
            earliest = (MISSES_TO_TRIP - 1) * CYCLE_S + timeout
            self.assertGreater(
                earliest, ladder,
                "%s: %s trips after %s s, before the %s s timeouts of "
                "#39" % (WATCHDOG, name, earliest, ladder))

    def test_the_es_trip_exceeds_the_sweeper(self):
        self.assertGreater(
            SECTION_TRIP_S, EvaluationService.SWEEPER_PERIOD_S,
            "%s: R3 must outlast ES's sweeper period" % WATCHDOG)

    def test_the_ps_trip_exceeds_one_request_and_the_retry_wait(self):
        self.assertGreater(
            SECTION_TRIP_S, CONNECT_TIMEOUT + READ_TIMEOUT,
            "%s: R3 for PS must outlast one request" % WATCHDOG)
        self.assertGreater(
            SECTION_TRIP_S, ProxyExecutor.MAX_RETRY_WAIT,
            "%s: R3 for PS must outlast PS's retry wait" % WATCHDOG)

    def test_the_worker_rule_waits_for_es_quit(self):
        self.assertGreater(
            WORKER_DISABLED_TRIP_S, FIRE_AND_FORGET_TIMEOUT,
            "%s: R4 must wait for ES's own quit" % WATCHDOG)


if __name__ == "__main__":
    unittest.main()
