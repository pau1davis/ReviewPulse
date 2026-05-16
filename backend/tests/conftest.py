"""
Shared pytest fixtures for integration tests.

Integration tests require a real PostgreSQL database with pgvector enabled.
Set TEST_DATABASE_URL in your environment (or .env) before running.
Tests are automatically skipped if it is not set.

Example:
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@localhost:5432/reviewpulse_test
"""
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    if not _TEST_DB_URL:
        pytest.skip("Set TEST_DATABASE_URL to run integration tests.")
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(test_engine):
    """Returns an async_sessionmaker bound to the test database."""
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(test_session_factory):
    """
    Yields a single test session. Does NOT roll back automatically — integration
    tests need committed data to be visible across sessions (the ingest task
    opens its own sessions internally).

    Each test should clean up its own data or use unique IDs.
    """
    async with test_session_factory() as session:
        yield session
