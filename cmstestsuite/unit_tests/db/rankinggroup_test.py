"""Tests for ranking groups, the new contest columns and their migration."""

import unittest
from unittest.mock import MagicMock

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import RankingGroup
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


if __name__ == "__main__":
    unittest.main()
