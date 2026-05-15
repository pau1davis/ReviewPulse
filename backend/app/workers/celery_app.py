from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "reviewpulse",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # ── Serialization ──────────────────────────────────────────────────────────
    # Never use pickle — it executes arbitrary code on deserialization.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # ── Timezone ──────────────────────────────────────────────────────────────
    timezone="UTC",
    enable_utc=True,
    # ── Result retention ──────────────────────────────────────────────────────
    result_expires=60 * 60 * 24,  # 24 hours — enough to poll job status
    # ── Reliability ───────────────────────────────────────────────────────────
    task_acks_late=True,          # only ack after the task succeeds, not on receipt
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, # one task at a time per worker slot — fair dispatch
    # ── Beat schedule: periodic re-ingestion ──────────────────────────────────
    # Runs at 02:00 UTC daily. Discovers new reviews for every book that has
    # had at least one completed ingestion job. Idempotent — duplicates are
    # skipped via the external_id unique constraint.
    beat_schedule={
        "refresh-all-books-daily": {
            "task": "app.workers.tasks.refresh_all_books",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)
