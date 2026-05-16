"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from alembic import op


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inline pgvector type — avoids importing the pgvector package at migration time.
# The DB just needs the vector extension installed (handled by op.execute below).
class _Vector(sa.types.UserDefinedType):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kwargs: object) -> str:
        return f"vector({self.dim})"


EMBEDDING_DIM = 768


def upgrade() -> None:
    # ── pgvector extension ────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── authors ───────────────────────────────────────────────────────────────
    op.create_table(
        "authors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("supabase_user_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("supabase_user_id", name="uq_authors_supabase_user_id"),
        sa.UniqueConstraint("email", name="uq_authors_email"),
    )
    op.create_index("ix_authors_supabase_user_id", "authors", ["supabase_user_id"])

    # ── books ─────────────────────────────────────────────────────────────────
    op.create_table(
        "books",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("author_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("isbn", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_books_author_id", "books", ["author_id"])

    # ── ingestion_jobs ────────────────────────────────────────────────────────
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.Column("reviews_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reviews_processed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_ingestion_jobs_book_id", "ingestion_jobs", ["book_id"])
    op.create_index("ix_ingestion_jobs_status", "ingestion_jobs", ["status"])

    # ── reviews ───────────────────────────────────────────────────────────────
    op.create_table(
        "reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("reviewer_name", sa.String(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("review_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", _Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "book_id", "external_id", name="uq_review_book_external"
        ),
    )
    op.create_index("ix_reviews_book_id", "reviews", ["book_id"])

    # HNSW index for fast approximate nearest-neighbour search (cosine distance)
    op.execute(
        "CREATE INDEX ix_reviews_embedding_hnsw ON reviews "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ── review_analyses ───────────────────────────────────────────────────────
    op.create_table(
        "review_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sentiment", sa.String(), nullable=False),
        sa.Column("sentiment_confidence", sa.Float(), nullable=False),
        sa.Column("themes", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column(
            "is_ai_generated", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "ai_generated_confidence",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "is_actionable", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"], ["reviews.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("review_id", name="uq_review_analyses_review_id"),
    )

    # ── webhook_endpoints ─────────────────────────────────────────────────────
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("author_id", UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_webhook_endpoints_author_id", "webhook_endpoints", ["author_id"]
    )

    # ── author_sessions ───────────────────────────────────────────────────────
    op.create_table(
        "author_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("author_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("author_id", name="uq_author_sessions_author_id"),
    )


def downgrade() -> None:
    op.drop_table("author_sessions")
    op.drop_table("webhook_endpoints")
    op.drop_table("review_analyses")
    op.execute("DROP INDEX IF EXISTS ix_reviews_embedding_hnsw")
    op.drop_table("reviews")
    op.drop_table("ingestion_jobs")
    op.drop_table("books")
    op.drop_table("authors")
    op.execute("DROP EXTENSION IF EXISTS vector")
