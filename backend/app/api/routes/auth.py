import uuid
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Author, AuthorSession
from app.db.session import get_db

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / response schemas ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterResponse(BaseModel):
    author_id: str
    email: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    author_id: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new author account.

    Calls the Supabase Admin API (service role key) to create the auth user
    with email_confirm=True so no confirmation email is required for the demo.
    In production you'd remove email_confirm and let Supabase send the email.
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
            json={
                "email": body.email,
                "password": body.password,
                "email_confirm": True,  # skip email verification for demo
            },
        )

    if r.status_code == 422:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    if r.status_code not in (200, 201):
        log.error("supabase.register_failed", status=r.status_code, body=r.text)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=r.json().get("message", "Registration failed."),
        )

    supabase_user_id = r.json()["id"]

    # Idempotent — if Author already exists (e.g. retry after partial failure),
    # return the existing record rather than erroring.
    result = await db.execute(
        select(Author).where(Author.supabase_user_id == supabase_user_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return RegisterResponse(author_id=str(existing.id), email=existing.email)

    author = Author(supabase_user_id=supabase_user_id, email=str(body.email))
    db.add(author)
    await db.flush()  # populate author.id before referencing it in AuthorSession

    db.add(AuthorSession(author_id=author.id))
    await db.commit()

    log.info("author.registered", author_id=str(author.id), email=author.email)
    return RegisterResponse(author_id=str(author.id), email=author.email)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Sign in and receive a Supabase JWT.
    The client must send this token as: Authorization: Bearer <access_token>
    on every subsequent request.
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{settings.supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": settings.supabase_anon_key},
            json={"email": str(body.email), "password": body.password},
        )

    if r.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    data = r.json()
    supabase_user_id = data["user"]["id"]

    result = await db.execute(
        select(Author)
        .where(Author.supabase_user_id == supabase_user_id)
        .options(selectinload(Author.session_info))
    )
    author = result.scalar_one_or_none()
    if not author:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found. Please POST /auth/register first.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Stamp last_seen_at — drives the "since you last logged in" feature
    if author.session_info:
        author.session_info.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    log.info("author.login", author_id=str(author.id))
    return LoginResponse(
        access_token=data["access_token"],
        expires_in=data.get("expires_in", 3600),
        author_id=str(author.id),
    )
