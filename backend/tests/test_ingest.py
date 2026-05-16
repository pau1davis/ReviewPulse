"""
Integration tests: ingest → analyze → store happy path + idempotency.

Requires TEST_DATABASE_URL. See conftest.py.
"""
import uuid

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.models import Author, Book, IngestionJob, Review, ReviewAnalysis
from app.workers.tasks import _run_ingest_book, _SyntheticReview
from tests.test_analysis import MockLLMProvider


# ── Shared fake review data ────────────────────────────────────────────────────

def _fake_reviews(n: int = 5) -> list[_SyntheticReview]:
    return [
        _SyntheticReview(
            reviewer_name=f"Reader {i}",
            rating=float((i % 5) + 1),
            body=(
                f"Review number {i}. The characters were compelling and the pacing "
                f"felt right. The ending left me wanting more. A truly great read."
            ),
            days_ago=(i + 1) * 10,
        )
        for i in range(n)
    ]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def author_and_book(db):
    """Creates a unique Author + Book row for each test."""
    uid = uuid.uuid4()
    author = Author(
        supabase_user_id=f"test-user-{uid}",
        email=f"test-{uid}@example.com",
    )
    db.add(author)
    await db.flush()

    book = Book(author_id=author.id, title=f"Test Novel {uid}")
    db.add(book)
    await db.flush()
    await db.commit()
    return author, book


@pytest_asyncio.fixture
async def patched_tasks(test_session_factory):
    """
    Context manager that patches the ingest task's session factory and LLM
    provider so they use the test database and mock LLM.
    """
    import os
    engine = create_async_engine(os.getenv("TEST_DATABASE_URL"), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch("app.workers.tasks.AsyncSessionLocal", factory),
        patch("app.workers.tasks.get_llm_provider", return_value=MockLLMProvider()),
        patch("app.workers.tasks._fire_webhooks"),  # don't need real webhooks in tests
    ):
        yield factory

    await engine.dispose()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_happy_path(author_and_book, patched_tasks, db):
    """
    Full pipeline: generate reviews → analyze → embed → store.
    Verifies that Review and ReviewAnalysis rows are created, embeddings are
    stored, and the job is marked completed.
    """
    _, book = author_and_book
    fake = _fake_reviews(5)

    job = IngestionJob(book_id=book.id)
    db.add(job)
    await db.commit()

    with patch("app.workers.tasks._generate_synthetic_reviews", return_value=fake):
        await _run_ingest_book(str(job.id), str(book.id))

    # Verify reviews were created
    async with patched_tasks() as session:
        reviews = (
            await session.execute(select(Review).where(Review.book_id == book.id))
        ).scalars().all()
        assert len(reviews) == len(fake), "Expected one row per generated review"

        for review in reviews:
            # Embedding should be stored
            assert review.embedding is not None
            assert len(review.embedding) == 768

            # Analysis should exist for every review
            analysis = (
                await session.execute(
                    select(ReviewAnalysis).where(ReviewAnalysis.review_id == review.id)
                )
            ).scalar_one_or_none()
            assert analysis is not None
            assert analysis.sentiment in ("positive", "mixed", "negative")
            assert 0.0 <= analysis.sentiment_confidence <= 1.0
            assert isinstance(analysis.themes, list)

        # Job should be completed with correct counts
        job_row = (
            await session.execute(
                select(IngestionJob).where(IngestionJob.id == job.id)
            )
        ).scalar_one()
        assert job_row.status == "completed"
        assert job_row.reviews_processed == len(fake)
        assert job_row.completed_at is not None


@pytest.mark.asyncio
async def test_ingest_idempotency(author_and_book, patched_tasks, db):
    """
    Running the same ingest job twice must not produce duplicate rows.

    The ON CONFLICT DO NOTHING constraint on (book_id, external_id) is what
    enforces this. This test proves it holds end-to-end.
    """
    _, book = author_and_book
    fake = _fake_reviews(5)

    for run in range(2):
        job = IngestionJob(book_id=book.id)
        db.add(job)
        await db.commit()

        with patch("app.workers.tasks._generate_synthetic_reviews", return_value=fake):
            await _run_ingest_book(str(job.id), str(book.id))

    # After two full runs, still exactly len(fake) reviews — no duplicates
    async with patched_tasks() as session:
        count = (
            await session.execute(
                select(func.count(Review.id)).where(Review.book_id == book.id)
            )
        ).scalar()
        assert count == len(fake), (
            f"Expected {len(fake)} reviews, got {count}. "
            "Idempotency constraint was not enforced."
        )

        analysis_count = (
            await session.execute(
                select(func.count(ReviewAnalysis.id))
                .join(Review, Review.id == ReviewAnalysis.review_id)
                .where(Review.book_id == book.id)
            )
        ).scalar()
        assert analysis_count == len(fake), "Duplicate analyses detected."


@pytest.mark.asyncio
async def test_failed_llm_marks_partial(author_and_book, db, test_session_factory):
    """
    If some reviews fail LLM analysis, the job is marked 'partial', not 'failed'.
    """
    import os
    from unittest.mock import AsyncMock

    _, book = author_and_book
    fake = _fake_reviews(4)

    engine = create_async_engine(os.getenv("TEST_DATABASE_URL"), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    failing_provider = MockLLMProvider()
    call_count = 0

    async def flaky_analyze(text):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            raise RuntimeError("Simulated LLM failure")
        from tests.test_analysis import VALID_ANALYSIS_PAYLOAD
        from app.llm.base import ReviewAnalysisResult
        return ReviewAnalysisResult.model_validate(VALID_ANALYSIS_PAYLOAD)

    failing_provider.analyze_review = flaky_analyze

    job = IngestionJob(book_id=book.id)
    db.add(job)
    await db.commit()

    with (
        patch("app.workers.tasks.AsyncSessionLocal", factory),
        patch("app.workers.tasks.get_llm_provider", return_value=failing_provider),
        patch("app.workers.tasks._fire_webhooks"),
        patch("app.workers.tasks._generate_synthetic_reviews", return_value=fake),
    ):
        await _run_ingest_book(str(job.id), str(book.id))

    async with factory() as session:
        job_row = (
            await session.execute(
                select(IngestionJob).where(IngestionJob.id == job.id)
            )
        ).scalar_one()
        assert job_row.status == "partial"
        assert job_row.reviews_processed > 0

    await engine.dispose()
