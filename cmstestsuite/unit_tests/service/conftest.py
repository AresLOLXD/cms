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

"""Fixtures shared by the tests of cms/service.

This file is loaded by both test groups (see
docker/_cms-test-internal.sh): it must not import cmscontrib or
cmsranking, nor patch anything with gevent.

"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import event

from cms.io.health import BusyTracker


@pytest.fixture(autouse=True)
def remove_statement_listeners(
        monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove, after each test, the engine listeners it added.

    EvaluationService.__init__ makes its BusyTracker count the
    statements of cms.db.engine (#38). Production builds one
    EvaluationService per process; a test process builds dozens, and
    each listener would stay on the engine and count every later
    statement of the process.

    monkeypatch: pytest's fixture, to wrap count_statements_of.

    """
    added: list[tuple[BusyTracker, Any]] = []
    count_statements_of = BusyTracker.count_statements_of

    def counting(tracker: BusyTracker, engine: Any) -> None:
        count_statements_of(tracker, engine)
        added.append((tracker, engine))

    monkeypatch.setattr(BusyTracker, "count_statements_of", counting)
    yield
    for tracker, engine in added:
        event.remove(engine, "before_cursor_execute", tracker._on_statement)
