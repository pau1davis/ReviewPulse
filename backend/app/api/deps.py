from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import extract_supabase_user_id
from app.db.models import Author
from app.db.session import get_db

# FastAPI will automatically parse "Authorization: Bearer <token>" headers.
# It returns 403 (not 401) if the header is missing entirely — that's FastAPI's
# default for HTTPBearer. Routes that should be public must not use this dependency.
bearer_scheme = HTTPBearer()


async def get_current_author(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Author:
    """
    FastAPI dependency that resolves a Supabase JWT to an Author row.

    Raises HTTP 401 if:
      - The token is invalid or expired
      - No Author row exists for the Supabase user (i.e. they haven't registered)

    Usage:
        @router.get("/books")
        async def list_books(author: Author = Depends(get_current_author)):
            ...

    Multi-tenant guarantee: the returned Author object is the only way routes
    know whose data to query. A route that filters by `author.id` can never
    accidentally return another author's data.
    """
    try:
        supabase_user_id = extract_supabase_user_id(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(Author).where(Author.supabase_user_id == supabase_user_id)
    )
    author = result.scalar_one_or_none()

    if author is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found for this user. POST /auth/register first.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return author
