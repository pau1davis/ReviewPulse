import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_author
from app.db.models import Author, Book, Review, ReviewAnalysis
from app.db.session import get_db

router = APIRouter(tags=["reviews"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AnalysisSummary(BaseModel):
    sentiment: str
    sentiment_confidence: float
    themes: list[str]
    is_ai_generated: bool
    ai_generated_confidence: float
    summary: str
    is_actionable: bool
    tokens_used: int
    cost_usd: float


class ReviewResponse(BaseModel):
    id: str
    external_id: str
    reviewer_name: str | None
    rating: float | None
    body: str
    review_date: str | None
    analysis: AnalysisSummary | None


class PaginatedReviews(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ReviewResponse]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/books/{book_id}/reviews", response_model=PaginatedReviews)
async def list_reviews(
    book_id: str,
    # Filters
    sentiment: Literal["positive", "mixed", "negative"] | None = Query(None),
    is_ai_generated: bool | None = Query(None),
    is_actionable: bool | None = Query(None),
    theme: str | None = Query(None, description="Filter reviews containing this theme"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Sort
    sort_by: Literal["review_date", "rating", "sentiment_confidence"] = Query("review_date"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    # Auth + DB
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    List reviews for a book with optional filters, pagination, and sorting.
    Only returns reviews for books owned by the authenticated author.
    """
    # Verify book belongs to this author
    book_result = await db.execute(
        select(Book).where(
            Book.id == uuid.UUID(book_id),
            Book.author_id == author.id,
        )
    )
    if not book_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found.")

    # Base query — left join so reviews without analysis are still returned
    base = (
        select(Review, ReviewAnalysis)
        .outerjoin(ReviewAnalysis, ReviewAnalysis.review_id == Review.id)
        .where(Review.book_id == uuid.UUID(book_id))
    )

    # Apply filters
    if sentiment is not None:
        base = base.where(ReviewAnalysis.sentiment == sentiment)
    if is_ai_generated is not None:
        base = base.where(ReviewAnalysis.is_ai_generated == is_ai_generated)
    if is_actionable is not None:
        base = base.where(ReviewAnalysis.is_actionable == is_actionable)
    if theme is not None:
        # Check if theme string is present in the ARRAY column
        base = base.where(ReviewAnalysis.themes.any(theme))
    if date_from is not None:
        base = base.where(Review.review_date >= date_from)
    if date_to is not None:
        base = base.where(Review.review_date <= date_to)

    # Count total matching rows for pagination metadata
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar()

    # Sort
    sort_col_map = {
        "review_date": Review.review_date,
        "rating": Review.rating,
        "sentiment_confidence": ReviewAnalysis.sentiment_confidence,
    }
    sort_col = sort_col_map[sort_by]
    ordered = base.order_by(
        sort_col.asc() if sort_order == "asc" else sort_col.desc()
    )

    # Paginate
    offset = (page - 1) * page_size
    paginated = ordered.offset(offset).limit(page_size)

    rows = (await db.execute(paginated)).all()

    results = []
    for review, analysis in rows:
        results.append(
            ReviewResponse(
                id=str(review.id),
                external_id=review.external_id,
                reviewer_name=review.reviewer_name,
                rating=review.rating,
                body=review.body,
                review_date=review.review_date.isoformat() if review.review_date else None,
                analysis=AnalysisSummary(
                    sentiment=analysis.sentiment,
                    sentiment_confidence=analysis.sentiment_confidence,
                    themes=analysis.themes,
                    is_ai_generated=analysis.is_ai_generated,
                    ai_generated_confidence=analysis.ai_generated_confidence,
                    summary=analysis.summary,
                    is_actionable=analysis.is_actionable,
                    tokens_used=analysis.tokens_used,
                    cost_usd=analysis.cost_usd,
                )
                if analysis
                else None,
            )
        )

    return PaginatedReviews(total=total, page=page, page_size=page_size, results=results)
