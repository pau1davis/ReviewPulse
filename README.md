# ReviewPulse

Review intelligence platform for independent authors. Ingests book reviews, analyses them with an LLM (sentiment, themes, AI-detection), surfaces trends, and delivers a weekly digest — all behind a FastAPI backend with a React frontend.

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI 0.115 + Uvicorn |
| Database | Supabase PostgreSQL + pgvector |
| ORM | SQLAlchemy 2.0 async (asyncpg) |
| Migrations | Alembic |
| Task queue | Celery 5 + Redis |
| LLM | Groq (llama-3.3-70b) **or** Gemini (gemini-2.0-flash) |
| Embeddings | Google text-embedding-004 (768 dims) |
| Auth | Supabase Auth — JWT (HS256) verified by the backend |
| Frontend | React 19 + TypeScript + Vite + Tailwind + Radix UI + Recharts |

---

## Prerequisites

- Python 3.12+
- Node.js 20+
- Docker + Docker Compose (for local Redis + optional containerised backend)
- A [Supabase](https://supabase.com) project with the **pgvector** extension enabled
- At least one of: [Groq API key](https://console.groq.com) or [Gemini API key](https://aistudio.google.com)

---

## Quick start (Docker Compose)

```bash
# 1. Clone and enter the repo
git clone https://github.com/yourname/reviewpulse.git
cd reviewpulse

# 2. Configure backend environment
cp backend/.env.example backend/.env
# Edit backend/.env — fill in Supabase credentials, API keys, etc.

# 3. Apply database migrations (requires psycopg2 reachable)
cd backend
pip install -r requirements.txt
alembic upgrade head
cd ..

# 4. Start all services
docker compose up --build
```

Services started:
- `backend` → http://localhost:8000 (FastAPI + auto-reload)
- `worker` → Celery worker (4 concurrent tasks)
- `beat` → Celery beat (daily refresh at 02:00 UTC)
- `redis` → Redis 7 on port 6379

---

## Manual setup (without Docker)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in values

# Apply migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload --port 8000

# In a separate terminal — start Celery worker
celery -A app.workers.celery_app worker --loglevel=info --concurrency=4

# In a third terminal — start Celery beat
celery -A app.workers.celery_app beat --loglevel=info
```

### Frontend

```bash
cd frontend
cp .env.example .env   # fill in VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY
npm install
npm run dev            # http://localhost:5173
```

The Vite dev server proxies all `/api/*` requests to `http://localhost:8000`, so CORS is never an issue locally.

---

## Environment variables

### Backend (`backend/.env`)

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon/public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (used for admin auth calls) |
| `SUPABASE_JWT_SECRET` | JWT secret from Supabase → Settings → API → JWT Secret |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` (session mode) |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `GROQ_API_KEY` | Groq API key (required if `LLM_PROVIDER=groq`) |
| `GEMINI_API_KEY` | Google AI Studio key (always required — used for embeddings) |
| `LLM_PROVIDER` | `groq` or `gemini` |
| `LLM_MODEL` | Model name override (defaults per provider) |
| `WEBHOOK_SECRET` | Random secret for HMAC-signing outbound webhooks |
| `CELERY_BROKER_URL` | Defaults to `REDIS_URL` |
| `CELERY_RESULT_BACKEND` | Defaults to `REDIS_URL` |

### Frontend (`frontend/.env`)

| Variable | Description |
|---|---|
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon/public key |

---

## Running tests

```bash
cd backend

# Unit tests only (no DB required)
pytest tests/test_analysis.py -v

# Integration tests (require a live Postgres DB)
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_reviewpulse \
  pytest tests/ -v
```

---

## Key API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Sign in, returns JWT |
| `GET` | `/books` | List catalog with metrics |
| `POST` | `/books` | Add book + trigger ingestion |
| `GET` | `/books/{id}` | Single book with metrics |
| `GET` | `/jobs/{id}` | Ingestion job status |
| `GET` | `/books/{id}/reviews` | Paginated, filtered reviews |
| `GET` | `/books/{id}/trends/sentiment` | Weekly sentiment series |
| `GET` | `/books/{id}/trends/themes` | Weekly theme series |
| `POST` | `/authors/me/compare` | Cross-book comparison |
| `POST` | `/authors/me/search` | Semantic search (pgvector) |
| `GET` | `/authors/me/digest` | 7-day digest |
| `GET` | `/authors/me/since-last-login` | New reviews since last session |
| `GET` | `/metrics` | Observability (DB, Redis, queue depth, LLM cost) |
| `GET` | `/healthz` | Liveness probe |

Full interactive docs at `http://localhost:8000/docs` (Swagger UI).

---

## Webhook events

When ingestion completes, ReviewPulse fires a signed POST to all active webhook endpoints registered for that author.

Verify the signature:
```python
import hmac, hashlib

def verify(payload: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)
```
