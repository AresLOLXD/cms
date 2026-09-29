"""Tests for ranking groups, the new contest columns and their migration."""

import json
import unittest
from unittest.mock import MagicMock

from sqlalchemy import select

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from cmstestsuite.unit_tests.filesystemmixin import FileSystemMixin

from cms.db import Contest, RankingGroup, Session, version as model_version
from cmscontrib.DumpImporter import DumpImporter
from cmscontrib.updaters.fork_multi_contest import \
    apply_fork_multi_contest_update


class TestRankingGroupModel(DatabaseMixin, unittest.TestCase):

    def test_contest_defaults(self):
        contest = self.add_contest()
        self.session.commit()
        self.assertFalse(contest.active)
        self.assertIsNone(contest.ranking_group_id)
        self.assertIsNone(contest.ranking_group)

    def test_contest_can_join_group(self):
        group = RankingGroup(name="olim", description="OLIM")
        self.session.add(group)
        contest = self.add_contest(active=True, ranking_group=group)
        self.session.commit()
        self.assertEqual(contest.ranking_group.name, "olim")

    def test_deleting_group_unsets_contests(self):
        group = RankingGroup(name="olim2", description="OLIM")
        self.session.add(group)
        contest = self.add_contest(ranking_group=group)
        self.session.commit()
        self.session.delete(group)
        self.session.commit()
        self.session.refresh(contest)
        self.assertIsNone(contest.ranking_group_id)

    def test_ranking_group_has_no_relationships(self):
        # DumpExporter follows every relationship: a backref to contests
        # would make exporting one contest drag every contest of its group.
        self.assertEqual(RankingGroup._rel_props, [])


class TestForkMigration(DatabaseMixin, unittest.TestCase):

    def test_update_is_idempotent(self):
        # init_db (run by DatabaseMixin) already created everything; the
        # update must be a no-op, also when applied twice.
        apply_fork_multi_contest_update()
        apply_fork_multi_contest_update()


class TestOldDumpCompatibility(unittest.TestCase):

    def test_old_dump_contest_gets_defaults(self):
        data = {"_class": "Contest", "name": "old", "description": "Old"}
        contest = DumpImporter.import_object(MagicMock(), data)
        self.assertFalse(contest.active)
        self.assertIsNone(contest.ranking_group_id)


class TestImportedContestsAreInactive(
        DatabaseMixin, FileSystemMixin, unittest.TestCase):
    """A dump must not make its contests visible to contestants by itself.

    Contest.active is exported like any other column, so importing a dump
    taken from a stack where the contest was live would publish it (and
    let its users log in) right away. Contests arrive inactive instead,
    except on a full restore (--drop), which keeps the flag of the dump.

    """

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    @staticmethod
    def make_dump(contests):
        """Build a dump whose root objects are the given contests.

        contests: the fields of each contest besides its name and
            description, indexed by the name of the contest.

        return: the dump, as the content of a contest.json.

        """
        dump = {"_version": model_version, "_objects": []}
        for name, fields in contests.items():
            dump[name] = {"_class": "Contest", "name": name,
                          "description": name, **fields}
            dump["_objects"].append(name)
        return dump

    @staticmethod
    def make_dump_with_dragged_contest():
        """Build a dump whose only root object is a user.

        Exporting a contest also exports its users, and through them the
        other contests they take part in: those are not listed in
        _objects. Here the contest "dragged" is reachable only through
        the participation of the user.

        return: the dump, as the content of a contest.json.

        """
        return {
            "user_key": {
                "_class": "User",
                "username": "username",
                "first_name": "First Name",
                "last_name": "Last Name",
                "password": "pwd",
                "participations": ["part_key"],
            },
            "part_key": {
                "_class": "Participation",
                "user": "user_key",
                "contest": "contest_key",
                "group": "group_key",
            },
            "contest_key": {
                "_class": "Contest",
                "name": "dragged",
                "description": "dragged along by a user",
                "active": True,
                "main_group": "group_key",
            },
            "group_key": {
                "_class": "Group",
                "contest": "contest_key",
                "name": "default",
            },
            "_version": model_version,
            "_objects": ["user_key"],
        }

    def import_dump(self, dump, drop=False, skip_users=False):
        """Write the dump to disk and import it.

        drop: whether to drop the database first (-d).
        skip_users: whether to leave the users out (-X).

        return: whether the import succeeded.

        """
        with open(self.get_path("contest.json"), "wt",
                  encoding="utf-8") as f:
            json.dump(dump, f)

        # The session is closed and reopened, otherwise the drop hangs.
        self.session.close()
        try:
            return DumpImporter(
                drop, self.base_dir, load_files=False, load_model=True,
                skip_generated=False, skip_submissions=False,
                skip_user_tests=False, skip_users=skip_users).do_import()
        finally:
            self.session = Session()

    def active_flags(self):
        """Return the active flag of each contest in the DB, by name."""
        return dict(self.session.execute(
            select(Contest.name, Contest.active)).all())

    @staticmethod
    def inactive_notices(logs):
        """Return the logged messages saying a contest arrived inactive."""
        return [message for message in (r.getMessage() for r in logs.records)
                if "inactive" in message]

    def test_active_contest_is_imported_inactive(self):
        dump = self.make_dump({"omips": {"active": True}})
        self.assertTrue(self.import_dump(dump))
        self.assertEqual(self.active_flags(), {"omips": False})

    def test_drop_keeps_the_active_flag_of_the_dump(self):
        dump = self.make_dump({"omips": {"active": True}})
        self.assertTrue(self.import_dump(dump, drop=True))
        self.assertEqual(self.active_flags(), {"omips": True})

    def test_contest_without_the_active_field_is_imported_inactive(self):
        # As in dumps made before the field existed.
        dump = self.make_dump({"old": {}})
        for drop in (False, True):
            with self.subTest(drop=drop):
                self.delete_data()
                self.assertTrue(self.import_dump(dump, drop=drop))
                self.assertEqual(self.active_flags(), {"old": False})

    def test_every_contest_of_the_dump_is_imported_inactive(self):
        dump = self.make_dump({"olim": {"active": True},
                               "omips": {"active": True}})
        self.assertTrue(self.import_dump(dump))
        self.assertEqual(self.active_flags(), {"olim": False, "omips": False})

    def test_contest_that_is_not_a_root_object_is_imported_inactive(self):
        # The contest is not listed in _objects, yet it is imported all
        # the same, dragged along by the user.
        dump = self.make_dump_with_dragged_contest()
        self.assertTrue(self.import_dump(dump))
        self.assertEqual(self.active_flags(), {"dragged": False})

    def test_each_imported_contest_is_reported_as_inactive(self):
        dump = self.make_dump({"olim": {"active": True}, "omips": {}})
        with self.assertLogs("cmscontrib.DumpImporter", level="INFO") as logs:
            self.assertTrue(self.import_dump(dump))

        notices = self.inactive_notices(logs)
        self.assertEqual(len(notices), 2)
        for name in ("olim", "omips"):
            with self.subTest(name):
                self.assertEqual(sum(name in n for n in notices), 1)
        for notice in notices:
            self.assertIn("AWS", notice)

    def test_contest_that_is_not_a_root_object_is_reported_as_inactive(self):
        dump = self.make_dump_with_dragged_contest()
        with self.assertLogs("cmscontrib.DumpImporter", level="INFO") as logs:
            self.assertTrue(self.import_dump(dump))

        notices = self.inactive_notices(logs)
        self.assertEqual(len(notices), 1)
        self.assertIn("dragged", notices[0])

    def test_contest_is_reported_as_inactive_also_without_users(self):
        dump = self.make_dump({"omips": {"active": True}})
        with self.assertLogs("cmscontrib.DumpImporter", level="INFO") as logs:
            self.assertTrue(self.import_dump(dump, skip_users=True))

        notices = self.inactive_notices(logs)
        self.assertEqual(len(notices), 1)
        self.assertIn("omips", notices[0])

    def test_contest_that_is_not_imported_is_not_reported_as_inactive(self):
        # Without its users the contest they drag along has nothing left
        # that leads to it, so it never reaches the DB. Reporting it as
        # imported would send the operator looking for it in AWS.
        dump = self.make_dump_with_dragged_contest()
        with self.assertLogs("cmscontrib.DumpImporter", level="INFO") as logs:
            self.assertTrue(self.import_dump(dump, skip_users=True))

        self.assertEqual(self.active_flags(), {})
        self.assertEqual(self.inactive_notices(logs), [])

    def test_nothing_is_reported_when_the_flag_of_the_dump_is_kept(self):
        dump = self.make_dump({"omips": {"active": True}})
        with self.assertLogs("cmscontrib.DumpImporter", level="INFO") as logs:
            self.assertTrue(self.import_dump(dump, drop=True))

        self.assertEqual(self.inactive_notices(logs), [])


if __name__ == "__main__":
    unittest.main()
