#!/bin/bash
# start.sh — runs Celery worker in the background, then starts uvicorn in the foreground.
# Used by the Render/Docker deployment so a single container serves both roles.
# In production at scale you'd split these into separate services.
set -e

echo "Starting Celery worker..."
celery -A app.workers.celery_app worker --loglevel=info --concurrency=2 &

echo "Starting Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
