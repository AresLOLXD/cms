#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
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

"""Service that checks the answering times of all services.

"""

import logging
import time

from cms import config, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient
from cms.io.async_service import AsyncService
from cms.io.rpc import RPCError


logger = logging.getLogger(__name__)


class Checker(AsyncService):
    """Service that checks the answering times of all services.

    """

    def __init__(self, shard):
        AsyncService.__init__(self, shard)
        for service in config.services:
            self.connect_to(service)
        self.add_timeout(self.check, None, 90.0, immediately=True)

        self.waiting_for = {}

    async def check(self):
        """For all services, send an echo request and logs the time of
        the request.

        """
        logger.debug("Checker.check")
        for coordinates, service in self.remote_services.items():
            if coordinates in self.waiting_for:
                logger.info("Service %s timeout, retrying.", coordinates)
                del self.waiting_for[coordinates]

            if service.connected:
                now = time.time()
                self.waiting_for[coordinates] = now
                self._spawn(self._echo_and_check(coordinates, service, now))
            else:
                logger.info("Service %s not connected.", coordinates)
        return True

    async def _echo_and_check(
        self,
        coordinates: ServiceCoord,
        service: AsyncRemoteServiceClient,
        now: float,
    ) -> None:
        """Send an echo request to service and handle its reply.

        coordinates: the coord of the service to echo.
        service: the remote service proxy to echo.
        now: the time at which the request is sent.

        """
        try:
            data = await service.echo(string="%s %5.3lf" % (coordinates, now))
        except RPCError:
            return
        self.echo_callback(data)

    def echo_callback(self, data):
        """Callback for check.

        """
        current = time.time()
        logger.debug("Checker.echo_callback")
        try:
            service, time_ = data.split()
            time_ = float(time_)
            name, shard = service.split(",")
            shard = int(shard)
            service = ServiceCoord(name, shard)
            if service not in self.waiting_for or current - time_ > 10:
                logger.warning("Got late reply (%5.3lf s) from %s.",
                               current - time_, service)
            else:
                if time_ - self.waiting_for[service] > 0.001:
                    logger.warning("Someone cheated on the timestamp?!")
                logger.info("Got reply (%5.3lf s) from %s.",
                            current - time_, service)
                del self.waiting_for[service]
        except KeyError:
            logger.error("Echo answer mis-shapen.")
