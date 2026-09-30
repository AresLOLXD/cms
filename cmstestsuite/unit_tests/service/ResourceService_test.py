#!/usr/bin/env python3

"""Tests for cms.service.ResourceService."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from cms.conf import Address, ServiceCoord
from cms.io.async_rpc import AsyncRemoteServiceClient, AsyncRemoteServiceServer
from cms.io.rpc import RPCError, rpc_method
from cms.service.ResourceService import ProcessMatcher, ResourceService
from cmstestsuite.unit_tests.servicelogmixin import \
    ServiceLoggingIsolationMixin


# Local services sharing a machine coordinate, as ResourceService.__init__
# requires (via _find_local_services) to build self._local_services.
LOG_SERVICE_COORD = ServiceCoord("LogService", 0)
RESOURCE_SERVICE_COORD = ServiceCoord("ResourceService", 0)
OTHER_SERVICE_COORD = ServiceCoord("OtherService", 0)
FAKE_SERVICES = {
    LOG_SERVICE_COORD: Address("127.0.0.1", 0),
    RESOURCE_SERVICE_COORD: Address("127.0.0.1", 0),
    OTHER_SERVICE_COORD: Address("127.0.0.1", 0),
}


# The real create_subprocess_exec, captured before any test patches
# cms.service.ResourceService.asyncio.create_subprocess_exec (which, since
# it's the same asyncio module object, would otherwise recurse into itself).
_real_create_subprocess_exec = asyncio.create_subprocess_exec


async def _launch_dummy_process(*args, **kwargs):
    """Stand-in for asyncio.create_subprocess_exec.

    Ignores the (nonexistent in the test environment) cms<Service>
    command line _restart_services would otherwise build, and launches a
    trivial, short-lived process instead, to exercise the real
    subprocess-launching path safely.

    args: ignored (the command line _restart_services built).
    kwargs: forwarded stdout/stderr redirections.

    return: the started process.

    """
    return await _real_create_subprocess_exec(
        sys.executable, "-c", "pass",
        stdout=kwargs.get("stdout"), stderr=kwargs.get("stderr"))


class QuittingLocalService:
    """A minimal stand-in for the remote side of a successful quit RPC."""

    @rpc_method
    def quit(self, reason: str) -> str:
        return "quit-sentinel"


class FailingQuitLocalService:
    """A stand-in whose quit RPC always fails, to trigger RPCError."""

    @rpc_method
    def quit(self, reason: str) -> str:
        raise ValueError("deliberate failure")


async def _start_server(local_service: object) -> tuple[asyncio.Server, int]:
    """Start a loopback server exposing local_service's RPC methods.

    local_service: object exposing the RPC methods to serve.

    return: the started server (must be closed by the caller) and the
        port it is listening on.

    """
    async def handle_client(reader, writer):
        server_side = AsyncRemoteServiceServer(
            local_service, Address("127.0.0.1", 0))
        server_side.initialize_streams(reader, writer, plus=None)
        await server_side.run()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    asyncio.create_task(server.serve_forever())
    return server, server.sockets[0].getsockname()[1]


class ResourceServiceTest(
    ServiceLoggingIsolationMixin, unittest.IsolatedAsyncioTestCase
):

    async def asyncSetUp(self):
        self._servers: list[asyncio.Server] = []
        self._clients: list[AsyncRemoteServiceClient] = []

        # ResourceService.__init__ needs at least one local service (itself)
        # in config.services to build self._local_services; tests use a
        # small fake set instead of the real (sample) config.
        config_patcher = patch(
            "cms.service.ResourceService.config.services", FAKE_SERVICES)
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

    async def asyncTearDown(self):
        for client in self._clients:
            client.disconnect()
        for server in self._servers:
            server.close()
            await server.wait_closed()

    async def _make_service(
        self, autorestart: bool = False, contest_id: int | None = None
    ) -> ResourceService:
        """Build a ResourceService with its periodic timers disabled.

        autorestart: forwarded to ResourceService.__init__.
        contest_id: forwarded to ResourceService.__init__.

        return: the constructed service.

        """
        # add_timeout is only used here to schedule the periodic
        # _store_resources/_restart_services calls; disabling it keeps
        # tests deterministic (no background reaping/launching racing
        # against the calls the tests make directly).
        with patch.object(
                ResourceService, "add_timeout", lambda *a, **k: None):
            service = ResourceService(
                shard=0, contest_id=contest_id, autorestart=autorestart)
        self.addCleanup(service._disconnect_all)
        return service

    async def _add_connected_service(
        self, service: ResourceService, coord: ServiceCoord,
        local_service: object
    ) -> AsyncRemoteServiceClient:
        """Wire a real, connected peer for coord into service.

        service: the ResourceService to register the peer into.
        coord: the coord to register the peer under.
        local_service: object exposing the RPC methods to serve.

        return: the connected client, already stored in
            service.remote_services[coord].

        """
        server, port = await _start_server(local_service)
        self._servers.append(server)

        with patch("cms.io.async_rpc.get_service_address",
                   return_value=Address("127.0.0.1", 0)):
            client = AsyncRemoteServiceClient(coord)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        client.initialize_streams(reader, writer, plus=None)
        asyncio.create_task(client.run())
        self._clients.append(client)

        service.remote_services[coord] = client
        return client

    async def _run_restart_services_and_capture_other_service_args(
        self, contest_id: int | None
    ) -> list[str]:
        """Run _restart_services for real and capture OtherService's argv.

        Points BIN_PATH at a temporary directory containing a real,
        executable cmsOtherService script that records the argv it was
        launched with, so the assertion exercises the actual *args
        unpacking behavior of _restart_services rather than a mock that
        ignores its arguments.

        contest_id: forwarded to the service under test.

        return: the argv the launched cmsOtherService script recorded.

        """
        with tempfile.TemporaryDirectory() as bin_path:
            script_path = os.path.join(bin_path, "cmsOtherService")
            args_path = os.path.join(bin_path, "args.json")
            with open(script_path, "w") as f:
                f.write(
                    "#!/usr/bin/env python3\n"
                    "import json\n"
                    "import sys\n"
                    f"with open({args_path!r}, 'w') as out:\n"
                    "    json.dump(sys.argv[1:], out)\n")
            os.chmod(script_path, 0o755)

            service = await self._make_service(
                autorestart=True, contest_id=contest_id)

            with patch.object(ProcessMatcher, "find", return_value=None), \
                    patch("cms.service.ResourceService.BIN_PATH", bin_path):
                await service._restart_services()
                await asyncio.gather(
                    *(p.wait() for p in service._launched_processes))

            with open(args_path) as f:
                return json.load(f)

    async def test_restart_services_launches_process_with_default_args(
        self,
    ):
        argv = await self._run_restart_services_and_capture_other_service_args(
            contest_id=None)

        self.assertEqual(argv, ["0", "-c", "ALL"])

    async def test_restart_services_launches_process_with_contest_id_args(
        self,
    ):
        argv = await self._run_restart_services_and_capture_other_service_args(
            contest_id=7)

        self.assertEqual(argv, ["0", "-c", "7"])

    async def test_restart_services_launches_process_for_missing_service(
        self,
    ):
        service = await self._make_service(autorestart=True)

        with patch.object(ProcessMatcher, "find", return_value=None), \
                patch("cms.service.ResourceService.asyncio."
                      "create_subprocess_exec", new=_launch_dummy_process):
            result = await service._restart_services()

        self.assertTrue(result)
        self.assertEqual(len(service._launched_processes), 1)
        launched = next(iter(service._launched_processes))
        self.assertIsInstance(launched, asyncio.subprocess.Process)

        # The dummy process exits almost immediately; let it finish.
        await launched.wait()

    async def test_restart_services_drops_exited_keeps_running_process(
        self,
    ):
        service = await self._make_service(autorestart=True)
        # Skip the launch loop entirely, to isolate the reaping logic.
        for coord in service._will_restart:
            service._will_restart[coord] = False

        exited_process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT)
        await exited_process.wait()

        running_process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(5)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT)

        async def cleanup_running_process():
            running_process.kill()
            await running_process.wait()

        self.addAsyncCleanup(cleanup_running_process)

        service._launched_processes = {exited_process, running_process}

        result = await service._restart_services()

        self.assertTrue(result)
        self.assertEqual(service._launched_processes, {running_process})

    async def test_restart_services_skips_log_and_resource_service(self):
        service = await self._make_service(autorestart=True)

        with patch.object(ProcessMatcher, "find", return_value=None) \
                as mock_find, \
                patch("cms.service.ResourceService.asyncio."
                      "create_subprocess_exec", new=_launch_dummy_process):
            await service._restart_services()
            await asyncio.gather(
                *(p.wait() for p in service._launched_processes))

        checked_names = {call.args[0].name for call in
                         mock_find.call_args_list}
        self.assertNotIn("LogService", checked_names)
        self.assertNotIn("ResourceService", checked_names)
        # A non-excluded local service must actually be checked by the
        # loop, otherwise this test would pass even if the loop did
        # nothing for every service.
        self.assertIn("OtherService", checked_names)

    async def test_restart_services_skips_service_not_scheduled(self):
        service = await self._make_service(autorestart=True)
        service._will_restart[OTHER_SERVICE_COORD] = False

        with patch.object(ProcessMatcher, "find") as mock_find:
            result = await service._restart_services()

        mock_find.assert_not_called()
        self.assertTrue(result)
        self.assertEqual(service._launched_processes, set())

    async def test_kill_service_returns_remote_quit_result(self):
        service = await self._make_service()
        coord = ServiceCoord("QuittingService", 0)
        await self._add_connected_service(
            service, coord, QuittingLocalService())

        result = await service.kill_service("QuittingService,0")

        self.assertEqual(result, "quit-sentinel")

    async def test_kill_service_propagates_rpc_error(self):
        service = await self._make_service()
        coord = ServiceCoord("FailingService", 0)
        await self._add_connected_service(
            service, coord, FailingQuitLocalService())

        with self.assertRaises(RPCError):
            await service.kill_service("FailingService,0")


if __name__ == "__main__":
    unittest.main()
