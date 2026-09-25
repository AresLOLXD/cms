#!/usr/bin/env python3

"""Tests for cms.db.async_session."""

import unittest
from unittest.mock import patch

from sqlalchemy import select

# Importing cms.io first exercises the realistic condition this
# sub-project's Review Focus item 1 calls out: a process that has both
# cms.io (which calls make_psycopg_green() unconditionally at import
# time, cms/io/__init__.py) and cms.db.async_session imported at once,
# same as any service mid-migration in sub-project 2.4 would. Importing
# is_psycopg_green (a cms.io submodule) imports the cms.io package, and
# asyncSetUp below checks the side effect actually happened.
from cms.io.PsycoGevent import is_psycopg_green
from cms.conf import config
from cms.db import RankingGroup
from cms.db.async_session import (
    AsyncSessionGen, _async_database_url, async_engine)
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class TestAsyncDatabaseUrl(unittest.TestCase):
    """Pure URL-derivation checks; no database connection is made."""

    def test_bare_postgresql_url_is_accepted(self):
        # SQLAlchemy resolves a bare postgresql:// URL to the psycopg2
        # driver, so it must be accepted just like the explicit form.
        with patch.object(config.database, "url",
                          "postgresql://user:s%40cret@host:5433/db"
                          "?sslmode=require"):
            url = _async_database_url()
        self.assertEqual(url.drivername, "postgresql+psycopg")
        self.assertEqual(url.password, "s@cret")
        self.assertEqual(url.host, "host")
        self.assertEqual(url.port, 5433)
        self.assertEqual(url.database, "db")
        self.assertEqual(dict(url.query), {"sslmode": "require"})

    def test_explicit_psycopg2_url_is_accepted(self):
        with patch.object(config.database, "url",
                          "postgresql+psycopg2://user@host/db"):
            url = _async_database_url()
        self.assertEqual(url.drivername, "postgresql+psycopg")

    def test_non_psycopg2_url_is_rejected(self):
        for bad_url in ("postgresql+asyncpg://user@host/db",
                        "sqlite:///db.sqlite"):
            with self.subTest(url=bad_url), \
                    patch.object(config.database, "url", bad_url):
                with self.assertRaises(AssertionError):
                    _async_database_url()


class TestAsyncSessionGenRoundTrip(DatabaseMixin,
                                  unittest.IsolatedAsyncioTestCase):
    # DatabaseMixin (re)creates the schema in setUpClass -- earlier
    # DatabaseMixin classes drop it in their tearDownClass, so this
    # class cannot assume it exists -- and asserts the configured DB
    # name ends in "fortesting" before anything is written to it.

    async def asyncSetUp(self):
        # Precondition for the "cms.io imported too" condition above.
        self.assertTrue(is_psycopg_green())

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
        # Each test runs on its own event loop; don't let pooled
        # connections outlive it (see cms.db.async_session's docstring).
        await async_engine.dispose()

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

    async def asyncTearDown(self):
        await async_engine.dispose()

    async def test_raises_when_twophase_commit_enabled(self):
        with patch.object(config.database, "twophase_commit", True):
            with self.assertRaises(NotImplementedError):
                async with AsyncSessionGen():
                    pass

    async def test_does_not_raise_when_twophase_commit_disabled(self):
        with patch.object(config.database, "twophase_commit", False):
            async with AsyncSessionGen() as session:
                self.assertIsNotNone(session)
