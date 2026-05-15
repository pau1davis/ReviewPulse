import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_author
from app.db.models import Author, Book, IngestionJob, Review, ReviewAnalysis
from app.db.session import get_db
from app.workers.tasks import ingest_book

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/books", tags=["books"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AddBookRequest(BaseModel):
    title: str
    isbn: str | None = None
    url: str | None = None


class JobSummary(BaseModel):
    job_id: str
    status: str
    reviews_found: int
    reviews_processed: int


class BookMetrics(BaseModel):
    review_count: int
    avg_rating: float | None
    sentiment_breakdown: dict[str, int]
    total_cost_usd: float
    latest_job: JobSummary | None


class BookResponse(BaseModel):
    id: str
    title: str
    isbn: str | None
    url: str | None
    created_at: str
    metrics: BookMetrics


class AddBookResponse(BaseModel):
    book: BookResponse
    job_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_book_metrics(db: AsyncSession, book_id: uuid.UUID) -> BookMetrics:
    # Review count + avg rating
    agg = await db.execute(
        select(
            func.count(Review.id).label("review_count"),
            func.avg(Review.rating).label("avg_rating"),
        ).where(Review.book_id == book_id)
    )
    row = agg.one()

    # Sentiment breakdown
    sent = await db.execute(
        select(ReviewAnalysis.sentiment, func.count(ReviewAnalysis.id))
        .join(Review, Review.id == ReviewAnalysis.review_id)
        .where(Review.book_id == book_id)
        .group_by(ReviewAnalysis.sentiment)
    )
    sentiment_breakdown = {"positive": 0, "mixed": 0, "negative": 0}
    for sentiment, count in sent.all():
        sentiment_breakdown[sentiment] = count

    # Total LLM cost for this book
    cost_row = await db.execute(
        select(func.coalesce(func.sum(ReviewAnalysis.cost_usd), 0.0))
        .join(Review, Review.id == ReviewAnalysis.review_id)
        .where(Review.book_id == book_id)
    )
    total_cost = float(cost_row.scalar())

    # Latest ingestion job
    job_row = await db.execute(
        select(IngestionJob)
        .where(IngestionJob.book_id == book_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    job = job_row.scalar_one_or_none()
    latest_job = (
        JobSummary(
            job_id=str(job.id),
            status=job.status,
            reviews_found=job.reviews_found,
            reviews_processed=job.reviews_processed,
        )
        if job
        else None
    )

    return BookMetrics(
        review_count=row.review_count,
        avg_rating=float(row.avg_rating) if row.avg_rating is not None else None,
        sentiment_breakdown=sentiment_breakdown,
        total_cost_usd=round(total_cost, 6),
        latest_job=latest_job,
    )


def _book_to_response(book: Book, metrics: BookMetrics) -> BookResponse:
    return BookResponse(
        id=str(book.id),
        title=book.title,
        isbn=book.isbn,
        url=book.url,
        created_at=book.created_at.isoformat(),
        metrics=metrics,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=AddBookResponse, status_code=status.HTTP_201_CREATED)
async def add_book(
    body: AddBookRequest,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a book to the author's catalog and immediately queue an ingestion job.
    Returns without waiting for ingestion to complete — poll GET /jobs/{job_id}
    to track progress.
    """
    book = Book(
        author_id=author.id,
        title=body.title,
        isbn=body.isbn,
        url=body.url,
    )
    db.add(book)
    await db.flush()

    job = IngestionJob(book_id=book.id)
    db.add(job)
    await db.flush()

    await db.commit()

    # Enqueue the Celery task — this returns immediately
    ingest_book.delay(str(job.id), str(book.id))

    log.info(
        "book.added",
        author_id=str(author.id),
        book_id=str(book.id),
        job_id=str(job.id),
    )

    metrics = await _get_book_metrics(db, book.id)
    return AddBookResponse(
        book=_book_to_response(book, metrics),
        job_id=str(job.id),
    )


@router.get("", response_model=list[BookResponse])
async def list_books(
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """Catalog view — all books for this author with key metrics."""
    result = await db.execute(
        select(Book)
        .where(Book.author_id == author.id)
        .order_by(Book.created_at.desc())
    )
    books = result.scalars().all()

    responses = []
    for book in books:
        metrics = await _get_book_metrics(db, book.id)
        responses.append(_book_to_response(book, metrics))
    return responses


@router.get("/{book_id}", response_model=BookResponse)
async def get_book(
    book_id: str,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """Single book detail. 404 if the book doesn't belong to this author."""
    result = await db.execute(
        select(Book).where(
            Book.id == uuid.UUID(book_id),
            Book.author_id == author.id,  # multi-tenant check
        )
    )
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found.")

    metrics = await _get_book_metrics(db, book.id)
    return _book_to_response(book, metrics)
