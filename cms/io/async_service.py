#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2019 Edoardo Morassutto <edoardo.morassutto@gmail.com>
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

"""Asyncio-based service base class, mirroring cms.io.service.Service.

"""

import asyncio
from collections.abc import Callable
import errno
import functools
import logging
import os
import signal
import socket
import time
from typing import Any

import gevent
import gevent.socket
from gevent.backdoor import BackdoorServer

from cms import ConfigError, config, mkdir, ServiceCoord, Address, \
    get_service_address
from cms.log import root_logger, shell_handler, ServiceFilter, \
    DetailedFormatter, LogServiceHandler, FileHandler
from cms.io.rpc import rpc_method
from .async_rpc import AsyncRemoteServiceServer, AsyncRemoteServiceClient, \
    AsyncFakeRemoteServiceClient, MAX_MESSAGE_SIZE


logger = logging.getLogger(__name__)


async def async_repeater(func: Callable[[], Any], period: float):
    """Repeatedly call the given (possibly async) function.

    See cms.io.service.repeater -- same contract, asyncio-native sleep.

    func: the function to call.
    period: the desired interval between successive calls.

    """
    while True:
        call = time.monotonic()

        try:
            result = func()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.error("Unexpected error.", exc_info=True)

        await asyncio.sleep(max(call + period - time.monotonic(), 0))


class AsyncService:

    def __init__(self, shard: int = 0):
        self.name = self.__class__.__name__
        self.shard = shard
        self._my_coord = ServiceCoord(self.name, self.shard)

        # Dictionaries of (to be) connected AsyncRemoteServiceClients.
        self.remote_services: dict[ServiceCoord, AsyncRemoteServiceClient] = {}

        self.initialize_logging()

        # We setup the listening address for services which want to
        # connect with us.
        try:
            self._listen_address = get_service_address(self._my_coord)
        except KeyError:
            raise ConfigError("Unable to find address for service %s. "
                              "Is it specified in core_services in cms.toml?" %
                              (self._my_coord,))

        self._server: asyncio.Server | None = None
        self.backdoor = None
        self._exit_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task] = set()

    def initialize_logging(self):
        """Set up additional logging handlers.

        What we do, in detail, is to add a logger to file (whose
        filename depends on the coords) and a remote logger to a
        LogService. We also attach the service coords to all log
        messages.

        """
        filter_ = ServiceFilter(self.name, self.shard)

        # Update shell handler to attach service coords.
        shell_handler.addFilter(filter_)

        # Determine location of log file, and make directories.
        log_dir = os.path.join(config.global_.log_dir,
                               "%s-%d" % (self.name, self.shard))
        mkdir(config.global_.log_dir)
        mkdir(log_dir)

        log_filename = time.strftime("%Y-%m-%d-%H-%M-%S.log")

        # Install a file handler.
        file_handler = FileHandler(os.path.join(log_dir, log_filename),
                                   mode='w', encoding='utf-8')
        if config.global_.file_log_debug:
            file_log_level = logging.DEBUG
        else:
            file_log_level = logging.INFO
        file_handler.setLevel(file_log_level)
        file_handler.setFormatter(DetailedFormatter(False))
        file_handler.addFilter(filter_)
        root_logger.addHandler(file_handler)

        # Provide a symlink to the latest log file.
        try:
            os.remove(os.path.join(log_dir, "last.log"))
        except OSError:
            pass
        os.symlink(log_filename, os.path.join(log_dir, "last.log"))

        # Setup a remote LogService handler (except when we already are
        # LogService, to avoid circular logging).
        if self.name != "LogService":
            log_service = self.connect_to(ServiceCoord("LogService", 0))
            remote_handler = LogServiceHandler(log_service)
            remote_handler.setLevel(logging.INFO)
            remote_handler.addFilter(filter_)
            root_logger.addHandler(remote_handler)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Receive and act upon an incoming connection.

        An AsyncRemoteServiceServer is spawned to take care of the new
        connection.

        """
        address = writer.get_extra_info("peername")
        remote_service = AsyncRemoteServiceServer(self, Address(address[0], address[1]))
        remote_service.initialize_streams(reader, writer, Address(address[0], address[1]))
        await remote_service.run()

    def connect_to(
        self,
        coord: ServiceCoord,
        on_connect: Callable[[object], Any] | None = None,
        on_disconnect: Callable[[object], Any] | None = None,
        must_be_present: bool = True,
    ) -> AsyncRemoteServiceClient:
        """Return a proxy to a remote service.

        Obtain a communication channel to the remote service at the
        given coord (reusing an existing one, if possible), attach the
        on_connect and on_disconnect handlers and return it.

        coord: the coord of the service to connect to.
        on_connect: to be called when the service connects.
        on_disconnect: to be called when it disconnects.
        must_be_present: if True, the coord must be present in
            the configuration; otherwise, it can be missing and in
            that case the return value is a fake client (that is, a
            client that never connects and ignores all calls).

        return: a proxy to that service.

        """
        if coord not in self.remote_services:
            try:
                service = AsyncRemoteServiceClient(coord, auto_retry=0.5)
            except KeyError:
                # The coordinates are invalid: raise a ConfigError if
                # the service was needed, or return a dummy client if
                # the service was optional.
                if must_be_present:
                    raise ConfigError("Missing address and port for %s "
                                      "in cms.toml." % (coord, ))
                else:
                    service = AsyncFakeRemoteServiceClient(coord, None)
            service.connect()
            self.remote_services[coord] = service
        else:
            service = self.remote_services[coord]

        if on_connect is not None:
            service.add_on_connect_handler(on_connect)

        if on_disconnect is not None:
            service.add_on_disconnect_handler(on_disconnect)

        return service

    def add_timeout(
        self,
        func: Callable,
        plus: dict | None,
        seconds: float,
        immediately: bool = False,
    ):
        """Register a function to be called repeatedly.

        func: the function to call.
        plus: additional data to pass to the function.
        seconds: the minimum interval between successive calls
            (may be larger if a call doesn't return on time).
        immediately: whether to call right off or wait also
            before the first call.

        """
        if plus is None:
            plus = {}
        func = functools.partial(func, **plus)

        async def start_after_delay():
            if not immediately:
                await asyncio.sleep(seconds)
            await async_repeater(func, seconds)

        task = asyncio.create_task(start_after_delay())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def exit(self):
        """Terminate the service at the next step.

        Safe to call from a signal handler (schedules the actual
        asyncio-touching work via call_soon_threadsafe) or as an
        ordinary method call from already-running async code.

        """
        logger.warning("%s received request to shut down.", self._my_coord)
        if self._loop is not None and self._exit_event is not None:
            self._loop.call_soon_threadsafe(self._exit_event.set)

    def get_backdoor_path(self) -> str:
        """Return the path for a UNIX domain socket to use as backdoor.

        """
        return os.path.join(config.global_.run_dir, "%s_%d" % (self.name, self.shard))

    @rpc_method
    def start_backdoor(self, backlog=50):
        """Start a backdoor server on a local UNIX domain socket.

        Deliberately still gevent-based (gevent.backdoor.BackdoorServer)
        -- an optional debugging REPL on its own independent UNIX
        socket, orthogonal to the RPC/event loop this class otherwise
        runs on asyncio. See the spec's Architecture section for why
        this is an accepted scoped simplification, not a gap.

        """
        backdoor_path = self.get_backdoor_path()
        try:
            os.remove(backdoor_path)
        except FileNotFoundError:
            pass
        else:
            logger.warning("A backdoor socket has been found and deleted.")
        mkdir(os.path.dirname(backdoor_path))
        backdoor_sock = gevent.socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        backdoor_sock.setblocking(0)
        backdoor_sock.bind(backdoor_path)
        os.chmod(backdoor_path, 0o700)
        backdoor_sock.listen(backlog)
        self.backdoor = BackdoorServer(backdoor_sock, locals={'service': self})
        self.backdoor.start()

    @rpc_method
    def stop_backdoor(self):
        """Stop a backdoor server started by start_backdoor.

        """
        if self.backdoor is not None:
            self.backdoor.stop()
        backdoor_path = self.get_backdoor_path()
        try:
            os.remove(backdoor_path)
        except FileNotFoundError:
            pass

    def run(self) -> bool:
        """Starts the main loop of the service (synchronous entry
        point, matching cms.io.service.Service.run's contract: blocks
        the calling thread until the service shuts down).

        return: True if successful.

        """
        return asyncio.run(self._async_run())

    async def _async_run(self) -> bool:
        self._loop = asyncio.get_running_loop()
        self._exit_event = asyncio.Event()

        self._loop.add_signal_handler(signal.SIGINT, self.exit)
        self._loop.add_signal_handler(signal.SIGTERM, self.exit)

        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                self._listen_address.ip, self._listen_address.port,
                limit=MAX_MESSAGE_SIZE)

        # This extends OSError and thus must come before it.
        except socket.gaierror:
            logger.critical("Service %s could not listen on "
                            "specified address, because it cannot "
                            "be resolved.", self.name)
            return False

        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                logger.critical("Listening port %s for service %s is "
                                "already in use, quitting.",
                                self._listen_address.port, self.name)
                return False
            elif error.errno == errno.EADDRNOTAVAIL:
                logger.critical("Service %s could not listen on "
                                "specified address, because it is not "
                                "available.", self.name)
                return False
            else:
                raise

        if config.global_.backdoor:
            self.start_backdoor()

        logger.info("%s %d up and running!", *self._my_coord)

        await self._exit_event.wait()

        logger.info("%s %d is shutting down", *self._my_coord)

        if config.global_.backdoor:
            self.stop_backdoor()

        self._server.close()
        await self._server.wait_closed()

        self._disconnect_all()
        return True

    def _disconnect_all(self):
        """Disconnect all remote services.

        """
        for service in self.remote_services.values():
            if service.connected:
                service.disconnect()

    @rpc_method
    def echo(self, string: str) -> str:
        """Simple RPC method.

        string: the string to be echoed.
        return: string, again.

        """
        return string

    @rpc_method
    def quit(self, reason: str = ""):
        """Shut down the service

        reason: why, oh why, you want me down?

        """
        logger.info("Trying to exit as asked by another service (%s).", reason)
        self.exit()
