import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_author
from app.db.models import Author, Book, IngestionJob
from app.db.session import get_db

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Schema ────────────────────────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    book_id: str
    book_title: str
    status: str          # queued | running | completed | failed | partial
    reviews_found: int
    reviews_processed: int
    error: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll the status of an ingestion job.

    The join through Book ensures Author A cannot poll Author B's jobs —
    if the job exists but belongs to a different author the response is 404,
    indistinguishable from a job that never existed.
    """
    result = await db.execute(
        select(IngestionJob, Book)
        .join(Book, Book.id == IngestionJob.book_id)
        .where(
            IngestionJob.id == uuid.UUID(job_id),
            Book.author_id == author.id,  # multi-tenant boundary
        )
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    job, book = row
    return JobStatusResponse(
        job_id=str(job.id),
        book_id=str(book.id),
        book_title=book.title,
        status=job.status,
        reviews_found=job.reviews_found,
        reviews_processed=job.reviews_processed,
        error=job.error,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
