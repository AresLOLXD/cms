#!/usr/bin/env python3

"""Tests for cms.db.async_session."""

import unittest
from unittest.mock import patch

from sqlalchemy import select

# Importing cms.io first exercises the realistic condition this
# sub-project's Review Focus item 1 calls out: a process that has both
# cms.io (which calls make_psycopg_green() unconditionally at import
# time, cms/io/__init__.py) and cms.db.async_session imported at once,
# same as any service mid-migration in sub-project 2.4 would.
import cms.io  # noqa: F401
from cms.conf import config
from cms.db import RankingGroup
from cms.db.async_session import AsyncSessionGen


class TestAsyncSessionGenRoundTrip(unittest.IsolatedAsyncioTestCase):

    async def asyncTearDown(self):
        # Best-effort cleanup so repeated runs don't collide on
        # RankingGroup.name's unique constraint.
        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            for row in result.scalars().all():
                await session.delete(row)
            await session.commit()

    async def test_insert_commit_and_read_back(self):
        self.name = "async-db-round-trip-commit-test"
        async with AsyncSessionGen() as session:
            session.add(RankingGroup(
                name=self.name, description="round-trip test"))
            await session.commit()

        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            group = result.scalar_one()
            self.assertEqual(group.description, "round-trip test")

    async def test_rollback_discards_uncommitted(self):
        self.name = "async-db-round-trip-rollback-test"
        async with AsyncSessionGen() as session:
            session.add(RankingGroup(
                name=self.name, description="should not persist"))
            # No commit -- AsyncSessionGen.__aexit__ must roll back.

        async with AsyncSessionGen() as session:
            result = await session.execute(
                select(RankingGroup).where(
                    RankingGroup.name == self.name))
            self.assertIsNone(result.scalar_one_or_none())


class TestAsyncSessionGenTwophaseGuard(unittest.IsolatedAsyncioTestCase):

    async def test_raises_when_twophase_commit_enabled(self):
        with patch.object(config.database, "twophase_commit", True):
            with self.assertRaises(NotImplementedError):
                async with AsyncSessionGen():
                    pass

    async def test_does_not_raise_when_twophase_commit_disabled(self):
        with patch.object(config.database, "twophase_commit", False):
            async with AsyncSessionGen() as session:
                self.assertIsNotNone(session)
