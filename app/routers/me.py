from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.deps import get_current_user
from app.models import User
from app.routers.games import build_dashboard
from app.schemas import DashboardOut, ProfileUpdate, UserOut

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> DashboardOut:
    return await build_dashboard(session, current)


@router.patch("/profile", response_model=UserOut)
async def patch_profile(
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> User:
    patch = body.model_dump(exclude_unset=True)
    if "upi_id" in patch:
        current.upi_id = patch["upi_id"]
    await session.commit()
    await session.refresh(current)
    return current
