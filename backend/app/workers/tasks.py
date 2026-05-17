import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.models import Book, IngestionJob, Review, ReviewAnalysis, WebhookEndpoint
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings as _settings

# Celery workers use asyncio.run() which creates a new event loop per task.
# NullPool avoids "Future attached to a different loop" by never reusing connections.
_celery_engine = create_async_engine(
    _settings.database_url,
    poolclass=NullPool,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(
    bind=_celery_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
from app.llm.base import get_llm_provider
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


# ── Synthetic review schema ────────────────────────────────────────────────────
# Used only for seeding; not part of the analysis pipeline.

class _SyntheticReview(BaseModel):
    reviewer_name: str
    rating: float          # 1.0 – 5.0
    body: str              # 50–200 words
    days_ago: int          # how many days ago the review was posted (1–365)


class _SyntheticBatch(BaseModel):
    reviews: list[_SyntheticReview]


# ── Synthetic review generation ────────────────────────────────────────────────

async def _generate_synthetic_reviews(
    book_title: str, count: int = 25
) -> list[_SyntheticReview]:
    """
    Ask the configured LLM to produce a batch of realistic synthetic reviews.
    Uses the provider's native structured-output API to guarantee valid JSON.
    """
    schema = _SyntheticBatch.model_json_schema()
    prompt = (
        f"Generate {count} realistic Amazon-style book reviews for a novel titled "
        f"'{book_title}'. Include a mix of 1–5 star ratings, varied reviewer names, "
        "detailed review bodies (50–200 words each), and days_ago values spread "
        "randomly between 1 and 365. Make reviews feel like genuine reader opinions."
    )

    provider_name = settings.llm_provider.lower()

    if provider_name == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.groq_api_key)
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "SyntheticBatch", "schema": schema, "strict": True},
            },
            temperature=0.9,
        )
        raw = response.choices[0].message.content
    else:
        import google.generativeai as genai
        model = genai.GenerativeModel(
            model_name=settings.llm_model,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=_SyntheticBatch,
                temperature=0.9,
            ),
        )
        response = await model.generate_content_async(prompt)
        raw = response.text

    batch = _SyntheticBatch.model_validate(json.loads(raw))
    return batch.reviews


# ── Webhook delivery ───────────────────────────────────────────────────────────

async def _fire_webhooks(
    session, book: Book, job: IngestionJob
) -> None:
    """
    HMAC-SHA256 signed POST to every active webhook endpoint for this author.
    Signature scheme: X-ReviewPulse-Signature: sha256=<hex>
    Body is the canonical JSON payload (no extra whitespace).
    """
    result = await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.author_id == book.author_id,
            WebhookEndpoint.is_active.is_(True),
        )
    )
    endpoints = result.scalars().all()
    if not endpoints:
        return

    payload = {
        "event": "ingestion.completed",
        "job_id": str(job.id),
        "book_id": str(book.id),
        "author_id": str(book.author_id),
        "reviews_found": job.reviews_found,
        "reviews_processed": job.reviews_processed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-ReviewPulse-Signature": f"sha256={sig}",
        "X-ReviewPulse-Event": "ingestion.completed",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for endpoint in endpoints:
            try:
                r = await client.post(
                    endpoint.url, content=payload_bytes, headers=headers
                )
                log.info(
                    "webhook.sent",
                    url=endpoint.url,
                    job_id=str(job.id),
                    status_code=r.status_code,
                )
            except Exception as exc:
                log.error("webhook.failed", url=endpoint.url, error=str(exc))


# ── Core pipeline (async) ──────────────────────────────────────────────────────

async def _run_ingest_book(job_id: str, book_id: str) -> None:
    provider = get_llm_provider()

    async with AsyncSessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        book = await session.get(Book, uuid.UUID(book_id))
        if not job or not book:
            log.error("ingest.not_found", job_id=job_id, book_id=book_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("ingest.started", job_id=job_id, book_title=book.title)

    # Generate synthetic reviews outside the main session to keep it short
    try:
        synthetic = await _generate_synthetic_reviews(book.title, count=25)
    except Exception as exc:
        async with AsyncSessionLocal() as session:
            job = await session.get(IngestionJob, uuid.UUID(job_id))
            job.status = "failed"
            job.error = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
        log.error("ingest.generate_failed", job_id=job_id, error=str(exc))
        return

    async with AsyncSessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        job.reviews_found = len(synthetic)
        await session.commit()

    processed = 0
    failed = 0

    for i, raw_review in enumerate(synthetic):
        external_id = f"synthetic_{book_id}_{i}"
        review_date = datetime.now(timezone.utc) - timedelta(days=raw_review.days_ago)

        async with AsyncSessionLocal() as session:
            # Idempotent insert — do nothing if this external_id already exists
            stmt = (
                pg_insert(Review)
                .values(
                    id=uuid.uuid4(),
                    book_id=uuid.UUID(book_id),
                    external_id=external_id,
                    reviewer_name=raw_review.reviewer_name,
                    rating=raw_review.rating,
                    body=raw_review.body,
                    review_date=review_date,
                )
                .on_conflict_do_nothing(constraint="uq_review_book_external")
                .returning(Review.id)
            )
            result = await session.execute(stmt)
            await session.commit()

            inserted_id = result.scalar_one_or_none()
            if inserted_id is None:
                log.info("ingest.review_skipped", external_id=external_id)
                processed += 1
                continue

        # Analyze and embed — each in its own session so a failure is isolated
        async with AsyncSessionLocal() as session:
            try:
                analysis = await provider.analyze_review(raw_review.body)

                # Approximate token count for cost tracking (avoids changing the
                # provider interface; acceptable for a take-home budget estimate)
                prompt_tokens = int(len(raw_review.body.split()) * 1.3) + 250
                completion_tokens = 120
                cost = provider.estimate_cost(prompt_tokens, completion_tokens)

                review_analysis = ReviewAnalysis(
                    review_id=inserted_id,
                    sentiment=analysis.sentiment,
                    sentiment_confidence=analysis.sentiment_confidence,
                    themes=analysis.themes,
                    is_ai_generated=analysis.is_ai_generated,
                    ai_generated_confidence=analysis.ai_generated_confidence,
                    summary=analysis.summary,
                    is_actionable=analysis.is_actionable,
                    tokens_used=prompt_tokens + completion_tokens,
                    cost_usd=cost,
                )
                session.add(review_analysis)

                embedding = await provider.embed_text(raw_review.body)
                await session.execute(
                    update(Review)
                    .where(Review.id == inserted_id)
                    .values(embedding=embedding)
                )

                await session.commit()
                processed += 1
                log.info(
                    "ingest.review_done",
                    review_id=str(inserted_id),
                    sentiment=analysis.sentiment,
                    cost_usd=round(cost, 6),
                )

            except Exception as exc:
                await session.rollback()
                failed += 1
                log.error(
                    "ingest.review_failed",
                    review_id=str(inserted_id),
                    external_id=external_id,
                    error=str(exc),
                )

        # Update progress after each review so the UI reflects real-time progress
        async with AsyncSessionLocal() as progress_session:
            await progress_session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == uuid.UUID(job_id))
                .values(reviews_processed=processed)
            )
            await progress_session.commit()

    # Finalise job
    async with AsyncSessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        job.reviews_processed = processed
        job.completed_at = datetime.now(timezone.utc)
        if failed == 0:
            job.status = "completed"
        elif processed > 0:
            job.status = "partial"
        else:
            job.status = "failed"
        await session.commit()

        log.info(
            "ingest.complete",
            job_id=job_id,
            status=job.status,
            processed=processed,
            failed=failed,
        )

        book = await session.get(Book, uuid.UUID(book_id))
        await _fire_webhooks(session, book, job)


async def _run_refresh_all_books() -> None:
    """
    Called by Celery beat daily. Finds every book that has ever completed
    ingestion and enqueues a fresh ingest job for it.
    New reviews get different synthetic_{book_id}_{i} IDs only if we
    increment the generation index — in a real system this would pull
    truly new reviews from the source. Here we skip re-generation if
    all external_ids already exist (idempotency handles it).
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Book.id)
            .join(IngestionJob, IngestionJob.book_id == Book.id)
            .where(IngestionJob.status == "completed")
            .distinct()
        )
        book_ids = [str(row[0]) for row in result.all()]

    for book_id in book_ids:
        async with AsyncSessionLocal() as session:
            new_job = IngestionJob(book_id=uuid.UUID(book_id))
            session.add(new_job)
            await session.commit()
            ingest_book.delay(str(new_job.id), book_id)
            log.info("refresh.enqueued", book_id=book_id, job_id=str(new_job.id))


# ── Celery task wrappers ───────────────────────────────────────────────────────
# Celery tasks must be synchronous functions. asyncio.run() is safe here
# because each Celery worker process runs one task at a time (prefetch=1).

@celery_app.task(bind=True, max_retries=2, name="app.workers.tasks.ingest_book")
def ingest_book(self, job_id: str, book_id: str) -> None:
    log.info("task.received", task="ingest_book", job_id=job_id, book_id=book_id)
    try:
        asyncio.run(_run_ingest_book(job_id, book_id))
    except Exception as exc:
        log.error("task.failed", task="ingest_book", job_id=job_id, error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.workers.tasks.refresh_all_books")
def refresh_all_books() -> None:
    log.info("task.received", task="refresh_all_books")
    asyncio.run(_run_refresh_all_books())
