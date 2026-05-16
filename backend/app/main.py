from contextlib import asynccontextmanager
from datetime import datetime, timezone

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal, engine

# Logging must be configured before any logger is created
setup_logging(log_level=settings.log_level, json_logs=settings.log_json)
log = structlog.get_logger(__name__)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("reviewpulse.starting", provider=settings.llm_provider)
    # Verify DB is reachable at startup — fail fast rather than accepting
    # requests that will all error.
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    log.info("reviewpulse.db_ok")
    yield
    await engine.dispose()
    log.info("reviewpulse.shutdown")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ReviewPulse",
    version="0.1.0",
    description="Review intelligence for independent authors.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://reviewpulse.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ───────────────────────────────────────────────────────────────────

from app.api.routes import auth, books, comparison, digest, jobs, reviews, search, trends

app.include_router(auth.router)
app.include_router(books.router)
app.include_router(jobs.router)
app.include_router(reviews.router)
app.include_router(search.router)
app.include_router(trends.router)
app.include_router(comparison.router)
app.include_router(digest.router)


# ── Observability (N12) ───────────────────────────────────────────────────────

@app.get("/metrics", tags=["admin"], include_in_schema=False)
async def metrics():
    """
    Simple observability endpoint — answers "is the system healthy and what is
    it doing?" without needing a Grafana board.

    Covers: DB connectivity, LLM provider in use, job queue depth (via Redis),
    and total spend to date. Useful at 3 AM when something is broken.
    """
    import redis.asyncio as aioredis
    from sqlalchemy import func, select
    from app.db.models import IngestionJob, ReviewAnalysis

    db_ok = False
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            pass

    redis_ok = False
    queue_depth = -1
    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        redis_ok = True
        queue_depth = await r.llen("celery")
        await r.aclose()
    except Exception:
        pass

    total_cost = 0.0
    job_counts: dict = {}
    async with AsyncSessionLocal() as session:
        try:
            cost_row = await session.execute(
                select(func.coalesce(func.sum(ReviewAnalysis.cost_usd), 0.0))
            )
            total_cost = float(cost_row.scalar())

            for s in ("queued", "running", "completed", "failed", "partial"):
                cnt = await session.execute(
                    select(func.count(IngestionJob.id)).where(IngestionJob.status == s)
                )
                job_counts[s] = cnt.scalar()
        except Exception:
            pass

    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "celery_queue_depth": queue_depth,
        "ingestion_jobs": job_counts,
        "total_llm_cost_usd": round(total_cost, 4),
    }


@app.get("/healthz", tags=["admin"], include_in_schema=False)
async def health():
    return {"status": "ok"}
