import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_author
from app.db.models import Author, AuthorSession, Book, Review, ReviewAnalysis
from app.db.session import get_db

router = APIRouter(tags=["digest"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DigestReview(BaseModel):
    review_id: str
    book_title: str
    reviewer_name: str | None
    rating: float | None
    snippet: str
    sentiment: str
    is_actionable: bool
    summary: str


class BookDigestSection(BaseModel):
    book_id: str
    title: str
    new_review_count: int
    positive: int
    mixed: int
    negative: int
    top_actionable: list[DigestReview]
    ai_flagged_count: int


class DigestResponse(BaseModel):
    period_start: str
    period_end: str
    total_new_reviews: int
    overall_sentiment_shift: str   # "improving" | "declining" | "stable"
    rising_themes: list[str]
    urgent_reviews: list[DigestReview]   # negative + actionable, all books
    books: list[BookDigestSection]


class SinceLastLoginResponse(BaseModel):
    last_seen_at: str
    total_new_reviews: int
    negative_reviews: list[DigestReview]
    actionable_reviews: list[DigestReview]
    ai_flagged_count: int
    review_count_by_book: dict[str, int]   # book_title → count


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _recent_reviews(
    db: AsyncSession, author_id: uuid.UUID, since: datetime, until: datetime
) -> list:
    """Fetch reviews + analysis + book title for a given time window."""
    return (
        await db.execute(
            select(Review, ReviewAnalysis, Book.title.label("book_title"))
            .join(Book, Book.id == Review.book_id)
            .outerjoin(ReviewAnalysis, ReviewAnalysis.review_id == Review.id)
            .where(
                Book.author_id == author_id,
                Review.review_date >= since,
                Review.review_date < until,
            )
            .order_by(ReviewAnalysis.sentiment.asc(), Review.review_date.desc())
        )
    ).all()


def _to_digest_review(row) -> DigestReview:
    review, analysis, book_title = row.Review, row.ReviewAnalysis, row.book_title
    return DigestReview(
        review_id=str(review.id),
        book_title=book_title,
        reviewer_name=review.reviewer_name,
        rating=review.rating,
        snippet=review.body[:200],
        sentiment=analysis.sentiment if analysis else "unknown",
        is_actionable=analysis.is_actionable if analysis else False,
        summary=analysis.summary if analysis else "",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/authors/me/digest", response_model=DigestResponse)
async def weekly_digest(
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Weekly digest preview — the email an author would receive each Monday.

    Covers the trailing 7-day window. Surfaces:
    - New reviews with sentiment breakdown per book
    - Urgent reviews (negative + actionable) across all books
    - Rising themes (appeared more this week than the previous week)
    - Overall sentiment direction vs. prior week
    """
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)

    this_week = await _recent_reviews(db, author.id, week_start, now)
    prev_week = await _recent_reviews(db, author.id, prev_week_start, week_start)

    # Sentiment shift
    def _sentiment_counts(rows):
        pos = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "positive")
        neg = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "negative")
        return pos, neg

    this_pos, this_neg = _sentiment_counts(this_week)
    prev_pos, prev_neg = _sentiment_counts(prev_week)

    this_total = len(this_week)
    prev_total = len(prev_week)
    this_rate = (this_pos - this_neg) / this_total if this_total else 0.0
    prev_rate = (prev_pos - prev_neg) / prev_total if prev_total else 0.0

    if this_rate > prev_rate + 0.1:
        sentiment_shift = "improving"
    elif this_rate < prev_rate - 0.1:
        sentiment_shift = "declining"
    else:
        sentiment_shift = "stable"

    # Rising themes — themes that appeared more this week than last
    def _theme_counts(rows) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            if row.ReviewAnalysis:
                for t in row.ReviewAnalysis.themes:
                    counts[t] = counts.get(t, 0) + 1
        return counts

    this_themes = _theme_counts(this_week)
    prev_themes = _theme_counts(prev_week)
    rising = sorted(
        [t for t in this_themes if this_themes[t] > prev_themes.get(t, 0)],
        key=lambda t: this_themes[t] - prev_themes.get(t, 0),
        reverse=True,
    )[:5]

    # Urgent reviews: negative + actionable across all books
    urgent = [
        _to_digest_review(r)
        for r in this_week
        if r.ReviewAnalysis
        and r.ReviewAnalysis.sentiment == "negative"
        and r.ReviewAnalysis.is_actionable
    ][:10]

    # Per-book sections
    books_seen: dict[str, list] = {}
    for row in this_week:
        bid = str(row.Review.book_id)
        books_seen.setdefault(bid, []).append(row)

    book_sections: list[BookDigestSection] = []
    for bid, rows in books_seen.items():
        pos = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "positive")
        mix = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "mixed")
        neg = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "negative")
        ai_ct = sum(1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.is_ai_generated)
        actionable = [
            _to_digest_review(r)
            for r in rows
            if r.ReviewAnalysis and r.ReviewAnalysis.is_actionable
        ][:3]
        book_sections.append(
            BookDigestSection(
                book_id=bid,
                title=rows[0].book_title,
                new_review_count=len(rows),
                positive=pos,
                mixed=mix,
                negative=neg,
                top_actionable=actionable,
                ai_flagged_count=ai_ct,
            )
        )

    return DigestResponse(
        period_start=week_start.date().isoformat(),
        period_end=now.date().isoformat(),
        total_new_reviews=len(this_week),
        overall_sentiment_shift=sentiment_shift,
        rising_themes=rising,
        urgent_reviews=urgent,
        books=book_sections,
    )


@router.get("/authors/me/since-last-login", response_model=SinceLastLoginResponse)
async def since_last_login(
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    What's new since the author last logged in.

    "Meaningful" is defined as:
      1. Negative reviews — most urgent; the author needs to know.
      2. Actionable reviews — something they could respond to or fix.
      3. AI-flagged reviews — suspicious activity worth awareness.

    last_seen_at is updated on every login (auth.py), so this always reflects
    activity since the previous session.
    """
    result = await db.execute(
        select(AuthorSession).where(AuthorSession.author_id == author.id)
    )
    session_record = result.scalar_one_or_none()
    last_seen = (
        session_record.last_seen_at
        if session_record
        else datetime.now(timezone.utc) - timedelta(days=7)
    )

    now = datetime.now(timezone.utc)
    rows = await _recent_reviews(db, author.id, last_seen, now)

    negative = [
        _to_digest_review(r)
        for r in rows
        if r.ReviewAnalysis and r.ReviewAnalysis.sentiment == "negative"
    ][:10]

    actionable = [
        _to_digest_review(r)
        for r in rows
        if r.ReviewAnalysis and r.ReviewAnalysis.is_actionable
    ][:10]

    ai_flagged = sum(
        1 for r in rows if r.ReviewAnalysis and r.ReviewAnalysis.is_ai_generated
    )

    # Count by book title for the summary header
    by_book: dict[str, int] = {}
    for row in rows:
        by_book[row.book_title] = by_book.get(row.book_title, 0) + 1

    return SinceLastLoginResponse(
        last_seen_at=last_seen.isoformat(),
        total_new_reviews=len(rows),
        negative_reviews=negative,
        actionable_reviews=actionable,
        ai_flagged_count=ai_flagged,
        review_count_by_book=by_book,
    )
