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

"""A unittest.TestCase mixin for tests instantiating an AsyncService.

AsyncService.__init__ (cms/io/async_service.py) mutates process-wide
logging state: it adds a filter to cms.log.shell_handler and a handler
to cms.log.root_logger, both module-level singletons shared by every
AsyncService instance in the process. In production this is harmless,
since a process hosts exactly one AsyncService for its whole lifetime.
In the test suite, though, every test that instantiates an AsyncService
subclass (directly, or via a concrete service such as Checker or
ResourceService) leaks a filter/handler that is never removed, so tests
from different files can pollute each other's log records when run in
the same pytest session. This mixin snapshots the polluted global state
before each test and restores it afterwards, isolating tests from one
another the same way DatabaseMixin isolates the DB schema.

"""

from cms.log import root_logger, shell_handler


class ServiceLoggingIsolationMixin:
    """Mixin that isolates tests from AsyncService's global logging state."""

    def setUp(self):
        super().setUp()
        self._logging_isolation_handlers = list(root_logger.handlers)
        self._logging_isolation_filters = list(shell_handler.filters)

    def tearDown(self):
        for handler in list(root_logger.handlers):
            if handler not in self._logging_isolation_handlers:
                handler.close()
                root_logger.removeHandler(handler)
        for filter_ in list(shell_handler.filters):
            if filter_ not in self._logging_isolation_filters:
                shell_handler.removeFilter(filter_)
        super().tearDown()
