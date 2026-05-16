import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_author
from app.db.models import Author, Book, Review, ReviewAnalysis
from app.db.session import get_db

router = APIRouter(tags=["comparison"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    book_ids: list[str] = Field(min_length=2, max_length=10)


class BookComparison(BaseModel):
    book_id: str
    title: str
    review_count: int
    avg_rating: float | None
    sentiment_distribution: dict[str, int]   # {"positive": N, "mixed": N, "negative": N}
    top_themes: list[str]                     # top 5 by frequency
    ai_flagged_rate: float                    # 0.0 – 1.0
    reviews_per_week: float                   # velocity


class CompareResponse(BaseModel):
    books: list[BookComparison]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/authors/me/compare", response_model=CompareResponse)
async def compare_books(
    body: CompareRequest,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Side-by-side comparison of N books (2–10).
    All requested books must belong to the authenticated author — any foreign
    or non-existent book ID causes a 404 for the whole request.
    """
    uuids = [uuid.UUID(bid) for bid in body.book_ids]

    # Verify ownership of all requested books in a single query
    result = await db.execute(
        select(Book).where(
            Book.id.in_(uuids),
            Book.author_id == author.id,
        )
    )
    books = result.scalars().all()

    if len(books) != len(uuids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more books not found or do not belong to you.",
        )

    comparisons: list[BookComparison] = []

    for book in books:
        # ── Aggregates ─────────────────────────────────────────────────────────
        agg = (await db.execute(
            select(
                func.count(Review.id).label("review_count"),
                func.avg(Review.rating).label("avg_rating"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "positive")
                .label("positive"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "mixed")
                .label("mixed"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "negative")
                .label("negative"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.is_ai_generated.is_(True))
                .label("ai_flagged"),
                func.min(Review.review_date).label("first_review"),
                func.max(Review.review_date).label("last_review"),
            )
            .outerjoin(ReviewAnalysis, ReviewAnalysis.review_id == Review.id)
            .where(Review.book_id == book.id)
        )).one()

        # ── Review velocity (reviews / week since first review) ────────────────
        if agg.first_review and agg.last_review and agg.first_review != agg.last_review:
            span_days = (agg.last_review - agg.first_review).days or 1
            reviews_per_week = round(agg.review_count / (span_days / 7), 2)
        else:
            reviews_per_week = float(agg.review_count)

        # ── AI flagged rate ────────────────────────────────────────────────────
        analyzed = agg.positive + agg.mixed + agg.negative
        ai_flagged_rate = round(agg.ai_flagged / analyzed, 4) if analyzed else 0.0

        # ── Top themes via unnest ──────────────────────────────────────────────
        theme_rows = (await db.execute(
            text("""
                SELECT unnest(ra.themes) AS theme, count(*)::int AS cnt
                FROM reviews r
                JOIN review_analyses ra ON ra.review_id = r.id
                WHERE r.book_id = :book_id
                GROUP BY theme
                ORDER BY cnt DESC
                LIMIT 5
            """),
            {"book_id": book.id},
        )).all()
        top_themes = [row.theme for row in theme_rows]

        comparisons.append(
            BookComparison(
                book_id=str(book.id),
                title=book.title,
                review_count=agg.review_count,
                avg_rating=round(float(agg.avg_rating), 2) if agg.avg_rating else None,
                sentiment_distribution={
                    "positive": agg.positive,
                    "mixed": agg.mixed,
                    "negative": agg.negative,
                },
                top_themes=top_themes,
                ai_flagged_rate=ai_flagged_rate,
                reviews_per_week=reviews_per_week,
            )
        )

    return CompareResponse(books=comparisons)
