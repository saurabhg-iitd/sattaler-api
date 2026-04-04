from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.deps import get_current_user
from app.live_game_buyin import buy_in_totals_from_events
from app.models import LiveGame, User
from app.routers.games import build_dashboard, load_accessible_groups
from app.schemas import DashboardOut, LiveGameSummaryOut, ProfileUpdate, UserOut

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/live-games", response_model=list[LiveGameSummaryOut])
async def list_my_live_games(
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> list[LiveGameSummaryOut]:
    """Live tables across groups you can access (for the Play tab)."""
    groups = await load_accessible_groups(session, current)
    if not groups:
        return []
    group_by_id = {g.id: g for g in groups}
    group_ids = list(group_by_id.keys())
    result = await session.execute(
        select(LiveGame)
        .options(
            selectinload(LiveGame.players),
            selectinload(LiveGame.buy_in_events),
        )
        .where(LiveGame.group_id.in_(group_ids))
        .order_by(LiveGame.updated_at.desc()),
    )
    games = list(result.scalars().unique().all())
    out: list[LiveGameSummaryOut] = []
    for lg in games:
        g = group_by_id.get(lg.group_id)
        if g is None:
            continue
        players = lg.players
        totals = buy_in_totals_from_events(lg.buy_in_events)
        out.append(
            LiveGameSummaryOut(
                id=lg.id,
                group_id=lg.group_id,
                group_name=g.name,
                rupees_per_coin=lg.rupees_per_coin,
                initial_buy_in_coins=lg.initial_buy_in_coins,
                player_count=len(players),
                total_buy_in_coins=sum(totals.values()),
                updated_at=lg.updated_at,
                created_at=lg.created_at,
            )
        )
    return out


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
