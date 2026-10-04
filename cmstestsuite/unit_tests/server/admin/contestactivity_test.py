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

"""Tests for the AWS pages of the participants' activity.

"""

import ipaddress
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import tornado.web

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import ActivityInterval, Participation
from cms.server.admin.handlers.contestactivity import \
    ContestActivityHandler, find_multiple_devices, \
    find_simultaneous_activity, parse_activity_filters, select_intervals
from cms.server.admin.handlers.contestuser import ParticipationHandler
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.util import Url


T0 = datetime(2026, 10, 12, 10, 0, 0)
LAPTOP = uuid.UUID("00000000-0000-4000-8000-000000000001")
PHONE = uuid.UUID("00000000-0000-4000-8000-000000000002")


def minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


class ActivityTestBase(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.contest = self.add_contest()
        self.participation = self.add_participation(contest=self.contest)
        self.session.flush()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def add_interval(self, participation: Participation,
                     device: uuid.UUID | None, ip: str,
                     start: float, end: float) -> ActivityInterval:
        interval = ActivityInterval(
            device_id=device, ip=ip, started_at=minutes(start),
            last_seen_at=minutes(end), started_by="resumed")
        interval.participation_id = participation.id
        self.session.add(interval)
        self.session.flush()
        return interval

    def simultaneous(self, participation_id=None):
        return find_simultaneous_activity(
            self.session, self.contest.id, participation_id)


class TestParseActivityFilters(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(parse_activity_filters(" ", ""), ("", None))

    def test_single_address_and_network(self):
        self.assertEqual(parse_activity_filters("u", "10.0.0.5"),
                         ("u", ipaddress.ip_network("10.0.0.5/32")))
        self.assertEqual(parse_activity_filters("", "10.0.0.7/24")[1],
                         ipaddress.ip_network("10.0.0.0/24"))

    def test_invalid_ip_filter_is_a_400(self):
        for text in ("abc", "10.0.0.", "10.0.0.0/33"):
            with self.subTest(text=text), \
                    self.assertRaises(tornado.web.HTTPError) as error:
                parse_activity_filters("", text)
            self.assertEqual(error.exception.status_code, 400)


class TestSelectIntervals(ActivityTestBase):

    def test_ip_is_plain_text_and_filters_work(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 10)
        self.add_interval(self.participation, LAPTOP, "2001:db8::1", 20, 30)
        other = self.add_participation()  # Another contest.
        self.session.flush()
        self.add_interval(other, LAPTOP, "10.0.0.5", 0, 10)

        def ips(username="", network=None):
            return sorted(row.ip for row in self.session.execute(
                select_intervals(self.contest.id, username, network)))

        self.assertEqual(ips(), ["10.0.0.5", "2001:db8::1"])
        self.assertEqual(ips(network=ipaddress.ip_network("10.0.0.0/24")),
                         ["10.0.0.5"])
        self.assertEqual(ips(network=ipaddress.ip_network("2001:db8::/32")),
                         ["2001:db8::1"])
        self.assertEqual(ips(username=self.participation.user.username),
                         ["10.0.0.5", "2001:db8::1"])
        self.assertEqual(ips(username="nobody"), [])


class TestSimultaneousActivity(ActivityTestBase):

    def test_two_devices_overlapping_are_reported(self):
        first = self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        second = self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)

        [row] = self.simultaneous()

        self.assertEqual({row.first_id, row.second_id}, {first.id, second.id})
        self.assertEqual((row.overlap_start, row.overlap_end),
                         (minutes(30), minutes(60)))
        self.assertEqual(row.username, self.participation.user.username)

    def test_dynamic_ip_change_on_one_device_is_not_reported(self):
        self.add_interval(self.participation, LAPTOP, "189.1.1.1", 0, 60)
        self.add_interval(self.participation, LAPTOP, "189.1.1.2", 60, 120)
        self.assertEqual(self.simultaneous(), [])

    def test_overlap_under_the_margin_is_not_reported(self):
        # The default margin is 2 minutes (twice the flush interval).
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 59, 90)
        self.assertEqual(self.simultaneous(), [])

    def test_overlap_equal_to_the_margin_is_not_reported(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 58, 90)
        self.assertEqual(self.simultaneous(), [])

    def test_overlap_just_over_the_margin_is_reported(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 57.99, 90)
        self.assertEqual(len(self.simultaneous()), 1)

    def test_overlap_is_found_whatever_the_order_of_the_ids(self):
        # The later interval in time has the lower id.
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 30, 90)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 60)
        # An interval inside another one.
        other = self.add_participation(contest=self.contest)
        self.session.flush()
        self.add_interval(other, LAPTOP, "10.0.0.7", 0, 60)
        self.add_interval(other, PHONE, "10.0.0.8", 10, 20)

        self.assertEqual(
            sorted((row.overlap_start, row.overlap_end)
                   for row in self.simultaneous()),
            [(minutes(10), minutes(20)), (minutes(30), minutes(60))])

    def test_interval_shorter_than_the_margin_is_not_reported(self):
        # Inside a longer interval: it overlaps it only for 1 minute.
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 10, 11)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 60)
        self.assertEqual(self.simultaneous(), [])

    def test_intervals_apart_in_time_are_not_reported(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 50, 90)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 30)
        self.assertEqual(self.simultaneous(), [])

    def test_limit_keeps_the_most_recent_and_count_tells_all(self):
        for start in (0, 100, 200):
            self.add_interval(self.participation, LAPTOP, "10.0.0.5",
                              start, start + 60)
            self.add_interval(self.participation, PHONE, "10.0.0.6",
                              start, start + 60)

        rows = find_simultaneous_activity(
            self.session, self.contest.id, limit=2)

        self.assertEqual([row.overlap_start for row in rows],
                         [minutes(200), minutes(100)])
        self.assertEqual([row.total for row in rows], [3, 3])

    def test_without_device_different_ips_are_reported(self):
        self.add_interval(self.participation, None, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, LAPTOP, "10.0.0.6", 0, 60)
        self.assertEqual(len(self.simultaneous()), 1)

    def test_without_device_same_ip_is_not_reported(self):
        self.add_interval(self.participation, None, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.assertEqual(self.simultaneous(), [])

    def test_filter_by_participation(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 60)
        other = self.add_participation(contest=self.contest)
        self.session.flush()

        self.assertEqual(len(self.simultaneous(self.participation.id)), 1)
        self.assertEqual(self.simultaneous(other.id), [])


class TestMultipleDevices(ActivityTestBase):

    def test_counts_distinct_non_null_devices(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 10)
        self.add_interval(self.participation, LAPTOP, "10.0.0.6", 20, 30)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 40, 50)
        single = self.add_participation(contest=self.contest)
        self.session.flush()
        self.add_interval(single, LAPTOP, "10.0.0.7", 0, 10)
        self.add_interval(single, None, "10.0.0.7", 0, 10)

        [row] = find_multiple_devices(self.session, self.contest.id)

        self.assertEqual(row.user_id, self.participation.user_id)
        self.assertEqual(row.devices, 2)


class PageTestBase(ActivityTestBase):
    """Build AWS handlers that render with the real templates."""

    def make_handler(self, handler_class=ContestActivityHandler,
                     **arguments):
        handler = handler_class.__new__(handler_class)
        handler.sql_session = self.session
        handler.contest = None
        handler.r_params = None
        handler._current_user = MagicMock(permission_all=True)
        handler._current_user.name = "admin"
        handler.request = MagicMock()
        handler.application = MagicMock()
        handler.application.service.jinja2_environment = AWS_ENVIRONMENT
        handler.static_url_helper = lambda *args, **kwargs: ""
        handler.url = Url("/")
        handler.xsrf_form_html = lambda: ""
        handler.get_query_argument = \
            lambda name, default=None: arguments.get(name, default)
        handler.chunks = []
        handler.write = handler.chunks.append
        return handler


class TestContestActivityPage(PageTestBase):

    def test_renders_alerts_and_intervals(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)
        self.session.commit()
        handler = self.make_handler(ip="189.1.1.0/24")

        handler._get_sync(str(self.contest.id))

        page = "".join(handler.chunks)
        self.assertIn("Simultaneous activity (1)", page)
        self.assertIn("More than one device (1)", page)
        self.assertIn("189.1.1.1", page)
        self.assertNotIn(">10.0.0.5<", page)  # Filtered out of the table.
        self.assertIn("activity/csv?ip=189.1.1.0%2F24", page)

    def test_simultaneous_activity_is_capped_with_its_total(self):
        for start in (0, 100):
            self.add_interval(self.participation, LAPTOP, "10.0.0.5",
                              start, start + 60)
            self.add_interval(self.participation, PHONE, "10.0.0.6",
                              start, start + 60)
        self.session.commit()
        handler = self.make_handler()

        with patch("cms.server.admin.handlers.contestactivity."
                   "SIMULTANEOUS_LIMIT", 1):
            handler._get_sync(str(self.contest.id))

        page = "".join(handler.chunks)
        self.assertIn("Simultaneous activity (2)", page)
        self.assertIn("Only the 1 most recent are shown", page)
        self.assertEqual(
            [row.overlap_start
             for row in handler.r_params["activity_simultaneous"]],
            [minutes(100)])

    def test_uncapped_simultaneous_activity_has_no_cap_notice(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "10.0.0.6", 0, 60)
        self.session.commit()
        handler = self.make_handler()

        handler._get_sync(str(self.contest.id))

        self.assertNotIn("most recent are shown", "".join(handler.chunks))

    def test_unknown_contest_is_a_404(self):
        with self.assertRaises(tornado.web.HTTPError) as error:
            self.make_handler()._get_sync("999999")
        self.assertEqual(error.exception.status_code, 404)


class TestParticipationPage(PageTestBase):
    """The participation page shows its own intervals and alerts."""

    def test_shows_only_this_participation(self):
        self.add_interval(self.participation, LAPTOP, "10.0.0.5", 0, 60)
        self.add_interval(self.participation, PHONE, "189.1.1.1", 30, 90)
        other = self.add_participation(contest=self.contest)
        self.session.flush()
        self.add_interval(other, LAPTOP, "172.16.0.9", 0, 60)
        self.session.commit()
        handler = self.make_handler(ParticipationHandler)

        handler._get_sync(str(self.contest.id),
                          str(self.participation.user_id))

        page = "".join(handler.chunks)
        self.assertIn("189.1.1.1", page)
        self.assertNotIn("172.16.0.9", page)
        self.assertEqual(len(handler.r_params["activity_intervals"]), 2)
        self.assertEqual(len(handler.r_params["activity_flagged_ids"]), 2)

    def test_simultaneous_activity_is_not_capped(self):
        for start in (0, 100):
            self.add_interval(self.participation, LAPTOP, "10.0.0.5",
                              start, start + 60)
            self.add_interval(self.participation, PHONE, "10.0.0.6",
                              start, start + 60)
        self.session.commit()
        handler = self.make_handler(ParticipationHandler)

        with patch("cms.server.admin.handlers.contestactivity."
                   "SIMULTANEOUS_LIMIT", 1):
            handler._get_sync(str(self.contest.id),
                              str(self.participation.user_id))

        self.assertEqual(len(handler.r_params["activity_simultaneous"]), 2)
