import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.deps import get_current_user
from app.email_norm import normalize_email
from app.group_access import can_access_group, get_group_eager
from app.models import Game, GamePlayerResult, LiveGame, PlayerGroup, User
from app.schemas import (
    DashboardOut,
    GameCreate,
    GameLineOut,
    GameOut,
    GameSummaryOut,
    GroupGamesSummaryOut,
)

router = APIRouter(prefix="/groups", tags=["games"])


def _session_profit_rupees(
    buy_in: int,
    remaining: int,
    rupees_per_coin: float,
    stake_lent: int,
    stake_borrowed: int,
    player_count: int,
    *,
    two_player_table_initial_buy_in: int | None = None,
) -> float:
    """P&L in rupees; 2p uses ±initial when peer stake exists; 3+ is chip vs buy-in only."""
    chip_delta = remaining - buy_in
    chip_cash = float(chip_delta) * rupees_per_coin
    extra = stake_lent - stake_borrowed
    if extra == 0:
        return chip_cash
    if player_count == 2 and two_player_table_initial_buy_in is not None:
        sign = 1.0 if chip_delta >= 0 else -1.0
        return chip_cash + sign * float(two_player_table_initial_buy_in) * rupees_per_coin
    return chip_cash


def _two_player_table_initial_buy_in(lines: list[GamePlayerResult]) -> int | None:
    """Min per-player bank-only book when exactly two lines (equal-start tables)."""
    if len(lines) != 2:
        return None
    adjs = [ln.buy_in_coins + ln.stake_lent_coins - ln.stake_borrowed_coins for ln in lines]
    return min(adjs)


def _email_local_part(email: str) -> str:
    i = email.find("@")
    return email[:i] if i > 0 else email


def _resolve_stored_display_name(
    *,
    email: str | None,
    user: User | None,
    client_display_name: str,
) -> str:
    """Prefer registered user's display_name, else email local-part, else client label."""
    client = client_display_name.strip()
    if email:
        if user is not None:
            dn = (user.display_name or "").strip()
            if dn:
                return dn[:200]
        return _email_local_part(email)[:200]
    return (client if client else "Player")[:200]


async def _users_by_email_map(session: AsyncSession, emails: list[str]) -> dict[str, User]:
    if not emails:
        return {}
    result = await session.execute(
        select(User).where(func.lower(User.email).in_(emails)),
    )
    out: dict[str, User] = {}
    for u in result.scalars().all():
        out[normalize_email(u.email)] = u
    return out


def game_to_out(game: Game) -> GameOut:
    lines = sorted(game.lines, key=lambda x: x.display_name.lower())
    n = len(lines)
    two_p_init = _two_player_table_initial_buy_in(lines)
    return GameOut(
        id=game.id,
        group_id=game.group_id,
        rupees_per_coin=game.rupees_per_coin,
        finished_at=game.finished_at,
        created_at=game.created_at,
        lines=[
            GameLineOut(
                email=ln.email,
                display_name=ln.display_name,
                buy_in_coins=ln.buy_in_coins,
                remaining_coins=ln.remaining_coins,
                stake_lent_coins=ln.stake_lent_coins,
                stake_borrowed_coins=ln.stake_borrowed_coins,
                profit_rupees=_session_profit_rupees(
                    ln.buy_in_coins,
                    ln.remaining_coins,
                    game.rupees_per_coin,
                    ln.stake_lent_coins,
                    ln.stake_borrowed_coins,
                    n,
                    two_player_table_initial_buy_in=two_p_init,
                ),
            )
            for ln in lines
        ],
    )


@router.get("/{group_id}/games", response_model=list[GameOut])
async def list_group_games(
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
) -> list[GameOut]:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.lines))
        .where(Game.group_id == group_id)
        .order_by(Game.finished_at.desc())
        .limit(limit)
    )
    games = list(result.scalars().unique().all())
    return [game_to_out(g) for g in games]


@router.post("/{group_id}/games", response_model=GameOut, status_code=status.HTTP_201_CREATED)
async def create_group_game(
    group_id: uuid.UUID,
    body: GameCreate,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GameOut:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    finished = body.finished_at or datetime.now(timezone.utc)
    game = Game(
        group_id=group_id,
        created_by_user_id=current.id,
        rupees_per_coin=body.rupees_per_coin,
        finished_at=finished,
    )
    session.add(game)
    await session.flush()
    line_emails: list[str | None] = [
        normalize_email(str(row.email)) if row.email is not None else None for row in body.lines
    ]
    unique_emails = list(dict.fromkeys(e for e in line_emails if e))
    users_map = await _users_by_email_map(session, unique_emails)
    for row, em in zip(body.lines, line_emails, strict=True):
        user = users_map.get(em) if em else None
        display = _resolve_stored_display_name(
            email=em,
            user=user,
            client_display_name=row.display_name,
        )
        session.add(
            GamePlayerResult(
                game_id=game.id,
                email=em,
                display_name=display,
                buy_in_coins=row.buy_in_coins,
                remaining_coins=row.remaining_coins,
                stake_lent_coins=row.stake_lent_coins,
                stake_borrowed_coins=row.stake_borrowed_coins,
            )
        )
    # Completed game is archived: remove any in-progress live tables for this group.
    await session.execute(delete(LiveGame).where(LiveGame.group_id == group_id))
    group.updated_at = datetime.now(timezone.utc)
    await session.commit()
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.lines))
        .where(Game.id == game.id)
    )
    loaded = result.scalar_one()
    return game_to_out(loaded)


@router.get("/{group_id}/games/{game_id}", response_model=GameOut)
async def get_group_game(
    group_id: uuid.UUID,
    game_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GameOut:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.lines))
        .where(Game.id == game_id, Game.group_id == group_id)
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return game_to_out(game)


@router.delete("/{group_id}/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group_game(
    group_id: uuid.UUID,
    game_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> None:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.owner_id != current.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group owner can delete saved games",
        )
    result = await session.execute(
        select(Game).where(Game.id == game_id, Game.group_id == group_id),
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    await session.delete(game)
    group.updated_at = datetime.now(timezone.utc)
    await session.commit()


def _my_profit_for_game(game: Game, user_email: str) -> float | None:
    me = normalize_email(user_email)
    lines = list(game.lines)
    n = len(lines)
    two_p_init = _two_player_table_initial_buy_in(lines)
    for ln in lines:
        if ln.email and normalize_email(ln.email) == me:
            return _session_profit_rupees(
                ln.buy_in_coins,
                ln.remaining_coins,
                game.rupees_per_coin,
                ln.stake_lent_coins,
                ln.stake_borrowed_coins,
                n,
                two_player_table_initial_buy_in=two_p_init,
            )
    return None


async def load_accessible_groups(session: AsyncSession, user: User) -> list[PlayerGroup]:
    from app.group_access import accessible_groups_where_clause

    result = await session.execute(
        select(PlayerGroup)
        .options(selectinload(PlayerGroup.member_rows))
        .where(accessible_groups_where_clause(user))
    )
    return list(result.scalars().unique().all())


async def build_dashboard(session: AsyncSession, user: User) -> DashboardOut:
    groups = await load_accessible_groups(session, user)
    if not groups:
        return DashboardOut(
            total_games=0,
            net_profit_rupees=0.0,
            by_group=[],
            recent_games=[],
        )
    group_ids = [g.id for g in groups]
    group_by_id = {g.id: g for g in groups}
    result = await session.execute(
        select(Game)
        .options(selectinload(Game.lines))
        .where(Game.group_id.in_(group_ids))
        .order_by(Game.finished_at.desc())
    )
    all_games = list(result.scalars().unique().all())

    me = normalize_email(user.email)
    my_games: list[Game] = []
    net_total = 0.0
    per_group: dict[uuid.UUID, tuple[int, float]] = {gid: (0, 0.0) for gid in group_ids}

    for game in all_games:
        profit = _my_profit_for_game(game, me)
        if profit is None:
            continue
        my_games.append(game)
        net_total += profit
        c, s = per_group.get(game.group_id, (0, 0.0))
        per_group[game.group_id] = (c + 1, s + profit)

    by_group: list[GroupGamesSummaryOut] = []
    for gid, (count, net) in per_group.items():
        if count == 0:
            continue
        g = group_by_id[gid]
        by_group.append(
            GroupGamesSummaryOut(
                group_id=gid,
                group_name=g.name,
                games_count=count,
                net_profit_rupees=round(net, 2),
            )
        )
    by_group.sort(key=lambda x: x.games_count, reverse=True)

    recent: list[GameSummaryOut] = []
    for game in sorted(my_games, key=lambda x: x.finished_at, reverse=True)[:20]:
        g = group_by_id.get(game.group_id)
        recent.append(
            GameSummaryOut(
                id=game.id,
                group_id=game.group_id,
                group_name=g.name if g else "",
                rupees_per_coin=game.rupees_per_coin,
                finished_at=game.finished_at,
                my_profit_rupees=_my_profit_for_game(game, me),
            )
        )

    return DashboardOut(
        total_games=len(my_games),
        net_profit_rupees=round(net_total, 2),
        by_group=by_group,
        recent_games=recent,
    )
