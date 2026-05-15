from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_author
from app.db.models import Author, Book, Review, ReviewAnalysis
from app.db.session import get_db
from app.llm.base import get_llm_provider

router = APIRouter(tags=["search"])

_SNIPPET_LEN = 250  # characters to include in result snippets


# ── Schemas ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=10, ge=1, le=50)


class SearchResult(BaseModel):
    review_id: str
    book_id: str
    book_title: str
    snippet: str
    score: float          # cosine similarity 0–1, higher = more similar
    reviewer_name: str | None
    sentiment: str | None


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/authors/me/search", response_model=list[SearchResult])
async def semantic_search(
    body: SearchRequest,
    author: Author = Depends(get_current_author),
    db: AsyncSession = Depends(get_db),
):
    """
    Semantic search across all reviews in the author's catalog.

    Embeds the query with the same model used at ingest time (Google
    text-embedding-004, 768 dims) and retrieves the top-K reviews by
    cosine similarity via pgvector.

    Only searches reviews belonging to this author's books (multi-tenant).
    Reviews without embeddings (not yet processed) are automatically excluded.
    """
    provider = get_llm_provider()

    try:
        query_embedding = await provider.embed_text(body.query)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding service unavailable: {exc}",
        )

    # pgvector cosine_distance = 1 - cosine_similarity.
    # ORDER BY distance ASC gives most-similar results first.
    # Multi-tenant boundary: join through Book and filter by author_id.
    distance_expr = Review.embedding.cosine_distance(query_embedding)

    stmt = (
        select(
            Review,
            Book.title.label("book_title"),
            distance_expr.label("distance"),
            ReviewAnalysis.sentiment,
        )
        .join(Book, Book.id == Review.book_id)
        .outerjoin(ReviewAnalysis, ReviewAnalysis.review_id == Review.id)
        .where(
            Book.author_id == author.id,   # ← multi-tenant boundary
            Review.embedding.is_not(None), # skip un-embedded reviews
        )
        .order_by(distance_expr.asc())
        .limit(body.k)
    )

    rows = (await db.execute(stmt)).all()

    return [
        SearchResult(
            review_id=str(row.Review.id),
            book_id=str(row.Review.book_id),
            book_title=row.book_title,
            snippet=row.Review.body[:_SNIPPET_LEN],
            score=round(1.0 - float(row.distance), 4),
            reviewer_name=row.Review.reviewer_name,
            sentiment=row.sentiment,
        )
        for row in rows
    ]
