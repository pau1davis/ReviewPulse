"""
Multi-tenancy isolation tests.

Proves that Author B's credentials cannot access Author A's data at the HTTP
layer — not just at the ORM layer. Every protected route is tested.

These are integration tests and require TEST_DATABASE_URL.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch

from app.core.config import settings
from app.db.models import Author, Book, IngestionJob
from app.db.session import get_db
from app.main import app

# A known secret used only in tests — never matches production
_TEST_JWT_SECRET = "reviewpulse-test-only-secret-do-not-use-in-prod"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_token(supabase_user_id: str) -> str:
    """Create a signed test JWT for a given Supabase user ID."""
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode(
        {
            "sub": supabase_user_id,
            "aud": "authenticated",
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def test_db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run multi-tenancy tests.")
    engine = create_async_engine(url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_db_engine):
    """
    HTTPX async test client with:
      - DB dependency overridden to use the test database
      - JWT secret patched to the test-only secret
    """
    factory = async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db

    with patch.object(settings, "supabase_jwt_secret", _TEST_JWT_SECRET):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def two_tenants(test_db_engine):
    """
    Creates two isolated authors (A and B) plus a book and job owned by A.
    Returns their tokens and IDs.
    """
    factory = async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )

    uid_a, uid_b = str(uuid.uuid4()), str(uuid.uuid4())

    async with factory() as session:
        author_a = Author(
            supabase_user_id=uid_a, email=f"author-a-{uid_a}@test.com"
        )
        author_b = Author(
            supabase_user_id=uid_b, email=f"author-b-{uid_b}@test.com"
        )
        session.add_all([author_a, author_b])
        await session.flush()

        book = Book(author_id=author_a.id, title="Author A's Private Novel")
        session.add(book)
        await session.flush()

        job = IngestionJob(book_id=book.id)
        session.add(job)
        await session.commit()

    return {
        "uid_a": uid_a,
        "uid_b": uid_b,
        "author_a_id": str(author_a.id),
        "author_b_id": str(author_b.id),
        "book_id": str(book.id),
        "job_id": str(job.id),
        "token_a": _make_token(uid_a),
        "token_b": _make_token(uid_b),
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestUnauthenticated:
    @pytest.mark.asyncio
    async def test_books_requires_auth(self, client):
        r = await client.get("/books")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_digest_requires_auth(self, client):
        r = await client.get("/authors/me/digest")
        assert r.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_search_requires_auth(self, client):
        r = await client.post("/authors/me/search", json={"query": "test"})
        assert r.status_code in (401, 403)


class TestCrossAuthorIsolation:
    @pytest.mark.asyncio
    async def test_author_a_can_read_own_book(self, client, two_tenants):
        """Sanity check: Author A can access their own book."""
        r = await client.get(
            f"/books/{two_tenants['book_id']}",
            headers={"Authorization": f"Bearer {two_tenants['token_a']}"},
        )
        assert r.status_code == 200
        assert r.json()["id"] == two_tenants["book_id"]

    @pytest.mark.asyncio
    async def test_author_b_cannot_read_author_a_book(self, client, two_tenants):
        """
        Author B using their valid JWT cannot retrieve Author A's book.
        Returns 404, not 403 — we don't leak that the resource exists.
        """
        r = await client.get(
            f"/books/{two_tenants['book_id']}",
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_author_b_cannot_list_author_a_reviews(self, client, two_tenants):
        r = await client.get(
            f"/books/{two_tenants['book_id']}/reviews",
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_author_b_cannot_poll_author_a_job(self, client, two_tenants):
        r = await client.get(
            f"/jobs/{two_tenants['job_id']}",
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_author_b_catalog_is_empty(self, client, two_tenants):
        """
        Author B's catalog is empty even though Author A has a book.
        GET /books returns [] for B, not A's books.
        """
        r = await client.get(
            "/books",
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_author_b_compare_blocked(self, client, two_tenants):
        """
        Author B cannot include Author A's book ID in a comparison request.
        """
        # Author B tries to compare Author A's book with a fake book
        r = await client.post(
            "/authors/me/compare",
            json={"book_ids": [two_tenants["book_id"], str(uuid.uuid4())]},
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_sentiment_trend_blocked_for_foreign_book(self, client, two_tenants):
        r = await client.get(
            f"/books/{two_tenants['book_id']}/trends/sentiment",
            headers={"Authorization": f"Bearer {two_tenants['token_b']}"},
        )
        assert r.status_code == 404
