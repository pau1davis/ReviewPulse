import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_author
from app.db.models import Author, Book, Review, ReviewAnalysis
from app.db.session import get_db

router = APIRouter(tags=["trends"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class SentimentWeek(BaseModel):
    week: str           # ISO date of week start (Monday)
    positive: int
    mixed: int
    negative: int
    total: int
    delta_positive: int  # change vs. previous week
    delta_negative: int


class ThemeWeek(BaseModel):
    week: str
    theme: str
    count: int


class SentimentTrendResponse(BaseModel):
    book_id: str
    series: list[SentimentWeek]


class ThemeTrendResponse(BaseModel):
    book_id: str
    series: list[ThemeWeek]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _verify_book_ownership(
    db: AsyncSession, book_id: str, author_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(Book).where(
            Book.id == uuid.UUID(book_id),
            Book.author_id == author_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/books/{book_id}/trends/sentiment", response_model=SentimentTrendResponse)
async def sentiment_trend(
    book_id: str,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Weekly sentiment time series for a book.
    Returns one row per week that has at least one review, with positive/mixed/
    negative counts and a week-over-week delta for positive and negative.
    """
    await _verify_book_ownership(db, book_id, author.id)

    week_expr = func.date_trunc("week", Review.review_date).label("week")

    rows = (
        await db.execute(
            select(
                week_expr,
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "positive")
                .label("positive"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "mixed")
                .label("mixed"),
                func.count(ReviewAnalysis.id)
                .filter(ReviewAnalysis.sentiment == "negative")
                .label("negative"),
                func.count(ReviewAnalysis.id).label("total"),
            )
            .join(ReviewAnalysis, ReviewAnalysis.review_id == Review.id)
            .where(
                Review.book_id == uuid.UUID(book_id),
                Review.review_date.is_not(None),
            )
            .group_by(week_expr)
            .order_by(week_expr.asc())
        )
    ).all()

    series: list[SentimentWeek] = []
    prev_positive = 0
    prev_negative = 0

    for row in rows:
        series.append(
            SentimentWeek(
                week=row.week.date().isoformat(),
                positive=row.positive,
                mixed=row.mixed,
                negative=row.negative,
                total=row.total,
                delta_positive=row.positive - prev_positive,
                delta_negative=row.negative - prev_negative,
            )
        )
        prev_positive = row.positive
        prev_negative = row.negative

    return SentimentTrendResponse(book_id=book_id, series=series)


@router.get("/books/{book_id}/trends/themes", response_model=ThemeTrendResponse)
async def theme_trend(
    book_id: str,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Theme frequency by week for a book.
    Uses PostgreSQL's unnest() to expand the themes array and group by week.
    Raw SQL is appropriate here — the ORM has no clean way to handle
    set-returning functions in GROUP BY.
    """
    await _verify_book_ownership(db, book_id, author.id)

    # Parameterized raw query — safe, no interpolation of user input
    stmt = text("""
        SELECT
            date_trunc('week', r.review_date)::date AS week,
            unnest(ra.themes)                        AS theme,
            count(*)::int                            AS count
        FROM reviews r
        JOIN review_analyses ra ON ra.review_id = r.id
        JOIN books b            ON b.id = r.book_id
        WHERE r.book_id   = :book_id
          AND b.author_id = :author_id
          AND r.review_date IS NOT NULL
        GROUP BY week, theme
        ORDER BY week ASC, count DESC
    """)

    rows = (
        await db.execute(
            stmt,
            {"book_id": uuid.UUID(book_id), "author_id": author.id},
        )
    ).all()

    series = [
        ThemeWeek(week=str(row.week), theme=row.theme, count=row.count)
        for row in rows
    ]
    return ThemeTrendResponse(book_id=book_id, series=series)
