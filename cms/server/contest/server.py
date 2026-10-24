#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2015 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015-2016 William Di Luigi <williamdiluigi@gmail.com>
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

"""ContestWebServer serves the webpage that contestants are using to:

- view information about the contest (times, ...);
- view tasks;
- view documentation (STL, ...);
- submit questions;
- view announcements and answer to questions;
- submit solutions;
- view the state and maybe the score of their submissions;
- release submissions to see their full score;
- query the test interface.

"""

import asyncio
from datetime import datetime, timedelta
import logging

from tornado.httputil import (
    ParseBodyConfig, ParseMultipartConfig, set_parse_body_config)

from cms import ConfigError, ServiceCoord, config
from cms.io import WebService
from cms.io.static_handler import MultiLocationStaticFileHandler
from cms.locale import get_translations
from cms.server.contest.jinja2_toolbox import CWS_ENVIRONMENT
from cmscommon.binary import hex_to_bin
from .activity import ActivityRecorder
from .handlers import HANDLERS
from .handlers.base import ContestListHandler
from .handlers.main import MainHandler


logger = logging.getLogger(__name__)


# How long the last flush of the activity log may take at shutdown
# (seconds), so that an unreachable database does not stop the service
# from exiting.
SHUTDOWN_FLUSH_TIMEOUT = 30

# Tornado parses the body of a form before it checks the XSRF token and
# before any handler runs, and rejects a multipart/form-data body with a
# 400 if it has more than 100 parts. The form that submits an OutputOnly
# task file by file has a part per output, on top of its _xsrf (and
# language) field, and a task can have hundreds of outputs. Tornado
# counts the empty preamble as a part, so this accepts 999 parts: the
# _xsrf field and up to 998 outputs (a few less for a BatchAndOutput
# task, whose form also sends the source file and the language). The
# urlencoded forms of CWS have a few fields, so their limit stays at
# Tornado's default.
MAX_MULTIPART_PARTS = 1000

# The most bytes of a multipart part's header. A legitimate header is
# under 500 bytes, and a long one costs the most to parse (about 20x a
# byte of data), so this keeps a single request from stalling the
# server for long, now that a body may have so many parts.
MAX_PART_HEADER_SIZE = 2048

# The most bytes of a request body, and the most seconds to read it.
# Tornado reads a whole body into memory before any handler runs, and
# without a limit it takes 100 MiB, on every connection at once. The
# largest legitimate body is a user test (up to 5 MB of input, plus
# the sources). This is a backstop, a body over it gets a bare 400: the
# precise limit is the reverse proxy's (Caddy's max_size), which must
# be lower than this one. A body that is not read in time closes the
# connection, with no response.
MAX_BODY_SIZE = 32 * 1024 * 1024
BODY_TIMEOUT = 300


class ContestWebServer(WebService):
    """Service that runs the web server serving the contestants.

    """
    def __init__(self, shard: int, contest_id: int | None = None):
        parameters = {
            "static_files": [("cms.server", "static"),
                             ("cms.server.contest", "static")],
            "cookie_secret": hex_to_bin(config.web_server.secret_key),
            "debug": config.web_server.tornado_debug,
            "is_proxy_used": None,
            "max_body_size": MAX_BODY_SIZE,
            "max_buffer_size": MAX_BODY_SIZE,
            "body_timeout": BODY_TIMEOUT,
            "num_proxies_used": config.contest_web_server.num_proxies_used,
            "xsrf_cookies": True,
        }

        try:
            listen_address = config.contest_web_server.listen_address[shard]
            listen_port = config.contest_web_server.listen_port[shard]
        except IndexError:
            raise ConfigError("Wrong shard number for %s, or missing "
                              "address/port configuration. Please check "
                              "listen_address and listen_port "
                              "in cms.toml." % __name__)

        self.contest_id = contest_id

        docs_location = (config.contest_web_server.docs_path
                         or config.contest_web_server.stl_path)
        handlers = [MultiLocationStaticFileHandler.make_route(
            r"/docs/(.*)", [docs_location])]

        if self.contest_id is None:
            HANDLERS.append((r"", MainHandler))
            handlers.append((r'/', ContestListHandler))
            for h in HANDLERS:
                handlers.append((r'/([^/]+)' + h[0],) + h[1:])
        else:
            HANDLERS.append((r"/", MainHandler))
            handlers.extend(HANDLERS)

        super().__init__(
            listen_port,
            handlers,
            parameters,
            shard=shard,
            listen_address=listen_address)

        self.jinja2_environment = CWS_ENVIRONMENT

        # Tornado's limits are global, but each service runs in its own
        # process, so these don't reach AWS. The call replaces the whole
        # configuration, so it leaves the urlencoded limit at the default.
        set_parse_body_config(ParseBodyConfig(
            multipart=ParseMultipartConfig(
                max_parts=MAX_MULTIPART_PARTS,
                max_part_header_size=MAX_PART_HEADER_SIZE)))

        # This is a dictionary (indexed by username) of pending
        # notification. Things like "Yay, your submission went
        # through.", not things like "Your question has been replied",
        # that are handled by the db. Each username points to a list
        # of tuples (timestamp, subject, text, level).
        self.notifications: dict[str, list[tuple[datetime, str, str, str]]] = {}

        # Retrieve the available translations.
        self.translations = get_translations()

        self.evaluation_service = self.connect_to(
            ServiceCoord("EvaluationService", 0))
        self.scoring_service = self.connect_to(
            ServiceCoord("ScoringService", 0))

        ranking_enabled = len(config.proxy_service.rankings) > 0
        self.proxy_service = self.connect_to(
            ServiceCoord("ProxyService", 0),
            must_be_present=ranking_enabled)

        self.activity_recorder = ActivityRecorder(timedelta(
            seconds=config.contest_web_server.activity_inactivity_threshold))
        self.add_timeout(self.activity_recorder.flush, None,
                         config.contest_web_server.activity_flush_interval)

        # Name of a header in which a trusted front proxy writes when it
        # received each request (see cms/server/request_time.py). Empty:
        # off.
        self.request_time_header: str = \
            config.contest_web_server.request_time_header

    async def _async_run(self) -> bool:
        try:
            return await super()._async_run()
        finally:
            # The HTTP server has stopped: store what is left.
            try:
                await asyncio.wait_for(self.activity_recorder.flush(),
                                       timeout=SHUTDOWN_FLUSH_TIMEOUT)
            except TimeoutError:
                logger.error(
                    "The participants' activity could not be stored "
                    "within %s seconds at shutdown; the activity of %d "
                    "participation(s) is lost.", SHUTDOWN_FLUSH_TIMEOUT,
                    len({key[0] for key
                         in self.activity_recorder.pending()}))

    def add_notification(
        self, username: str, timestamp: datetime, subject: str, text: str, level: str
    ):
        """Store a new notification to send to a user at the first
        opportunity (i.e., at the first request fot db notifications).

        username: the user to notify.
        timestamp: the time of the notification.
        subject: subject of the notification.
        text: body of the notification.
        level: one of NOTIFICATION_* (defined above)

        """
        if username not in self.notifications:
            self.notifications[username] = []
        self.notifications[username].append((timestamp, subject, text, level))
