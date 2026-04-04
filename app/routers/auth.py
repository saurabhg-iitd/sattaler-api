from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.email_norm import normalize_email
from app.database import get_session
from app.deps import get_current_user
from app.models import User
from app.schemas import GoogleAuthRequest, TokenResponse, UserOut
from app.security import create_access_token

router = APIRouter(tags=["auth"])


@router.post("/auth/google", response_model=TokenResponse)
async def exchange_google_token(
    body: GoogleAuthRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server missing GOOGLE_CLIENT_ID",
        )
    try:
        info = id_token.verify_oauth2_token(
            body.id_token,
            Request(),
            settings.google_client_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google ID token",
        )

    sub = info.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="Token missing sub")

    email = normalize_email(
        (info.get("email") or "").strip() or f"{sub}@placeholder.local",
    )
    display_name = info.get("name")

    result = await session.execute(select(User).where(User.google_sub == sub))
    user = result.scalar_one_or_none()
    if user:
        user.email = email
        if display_name:
            user.display_name = display_name
    else:
        user = User(google_sub=sub, email=email, display_name=display_name)
        session.add(user)

    await session.commit()
    await session.refresh(user)
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.get("/auth/me", response_model=UserOut)
async def me(current: User = Depends(get_current_user)) -> User:
    return current
