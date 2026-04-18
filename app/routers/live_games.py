import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.email_norm import normalize_email
from app.live_game_buyin import buy_in_totals_from_events
from app.deps import get_current_user
from app.group_access import can_access_group, get_group_eager
from app.models import LiveGame, LiveGameBuyInEvent, LiveGamePlayer, PlayerGroup, User
from app.schemas import (
    LiveGameBuyInEventIn,
    LiveGameBuyInEventOut,
    LiveGameCreate,
    LiveGameOut,
    LiveGamePatch,
    LiveGamePlayerOut,
)

router = APIRouter(prefix="/groups", tags=["live-games"])


def _validate_create_players(body: LiveGameCreate) -> None:
    ids = {p.client_player_id for p in body.players}
    if len(ids) != len(body.players):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate client_player_id in players",
        )
    for p in body.players:
        if p.initial_buy_in_source == "player_transfer":
            fid = p.from_client_player_id
            if not fid or fid == p.client_player_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="player_transfer requires from_client_player_id different from target",
                )
            if fid not in ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown from_client_player_id: {fid}",
                )


def _validate_patch_events(events: list[LiveGameBuyInEventIn], known_ids: set[str]) -> None:
    for ev in events:
        if ev.target_client_player_id not in known_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown target_client_player_id: {ev.target_client_player_id}",
            )
        if ev.event_kind == "player_transfer":
            if not ev.from_client_player_id or ev.from_client_player_id == ev.target_client_player_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="player_transfer events require from_client_player_id",
                )
            if ev.from_client_player_id not in known_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown from_client_player_id: {ev.from_client_player_id}",
                )
        elif ev.event_kind in ("bank", "bank_return"):
            if ev.from_client_player_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{ev.event_kind} events must not set from_client_player_id",
                )


def _live_game_to_out(game: LiveGame) -> LiveGameOut:
    players = sorted(game.players, key=lambda x: x.display_name.lower())
    events = sorted(game.buy_in_events, key=lambda x: x.created_at)
    buy_in_by_client = buy_in_totals_from_events(game.buy_in_events)
    return LiveGameOut(
        id=game.id,
        group_id=game.group_id,
        rupees_per_coin=game.rupees_per_coin,
        initial_buy_in_coins=game.initial_buy_in_coins,
        created_at=game.created_at,
        updated_at=game.updated_at,
        players=[
            LiveGamePlayerOut(
                client_player_id=p.client_player_id,
                email=p.email,
                display_name=p.display_name,
                buy_in_coins=buy_in_by_client.get(p.client_player_id, 0),
            )
            for p in players
        ],
        buy_in_events=[
            LiveGameBuyInEventOut(
                id=e.id,
                created_at=e.created_at,
                event_kind=e.event_kind,
                target_client_player_id=e.target_client_player_id,
                coins=e.coins,
                from_client_player_id=e.from_client_player_id,
            )
            for e in events
        ],
    )


async def _sync_player_buy_ins_from_db_events(
    session: AsyncSession,
    live_game_id: uuid.UUID,
    players: list[LiveGamePlayer],
) -> None:
    """Refresh [LiveGamePlayer.buy_in_coins] from all stored buy-in events."""
    result = await session.execute(
        select(LiveGameBuyInEvent).where(LiveGameBuyInEvent.live_game_id == live_game_id),
    )
    evs = list(result.scalars().all())
    totals = buy_in_totals_from_events(evs)
    for p in players:
        p.buy_in_coins = totals.get(p.client_player_id, 0)


@router.post(
    "/{group_id}/live-games",
    response_model=LiveGameOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_live_game(
    group_id: uuid.UUID,
    body: LiveGameCreate,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> LiveGameOut:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    _validate_create_players(body)

    now = datetime.now(timezone.utc)
    game = LiveGame(
        group_id=group_id,
        created_by_user_id=current.id,
        rupees_per_coin=body.rupees_per_coin,
        initial_buy_in_coins=body.initial_buy_in_coins,
        updated_at=now,
    )
    session.add(game)
    await session.flush()

    # Keep explicit references — do not use `game.players` here: lazy-loading that
    # relationship on AsyncSession raises MissingGreenlet → 500.
    added_players: list[LiveGamePlayer] = []
    for row in body.players:
        lp = LiveGamePlayer(
            live_game_id=game.id,
            client_player_id=row.client_player_id,
            email=row.email,
            display_name=row.display_name.strip()[:200],
            buy_in_coins=body.initial_buy_in_coins,
        )
        session.add(lp)
        added_players.append(lp)

    for row in body.players:
        ev = LiveGameBuyInEvent(
            live_game_id=game.id,
            event_kind="initial",
            target_client_player_id=row.client_player_id,
            coins=body.initial_buy_in_coins,
            from_client_player_id=row.from_client_player_id
            if row.initial_buy_in_source == "player_transfer"
            else None,
        )
        session.add(ev)

    await session.flush()
    await _sync_player_buy_ins_from_db_events(session, game.id, added_players)

    group.updated_at = now
    await session.commit()

    result = await session.execute(
        select(LiveGame)
        .options(
            selectinload(LiveGame.players),
            selectinload(LiveGame.buy_in_events),
        )
        .where(LiveGame.id == game.id),
    )
    loaded = result.scalar_one()
    return _live_game_to_out(loaded)


async def _get_live_game_owned(
    session: AsyncSession,
    group_id: uuid.UUID,
    live_game_id: uuid.UUID,
    current: User,
) -> LiveGame:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(LiveGame)
        .options(
            selectinload(LiveGame.players),
            selectinload(LiveGame.buy_in_events),
        )
        .where(LiveGame.id == live_game_id, LiveGame.group_id == group_id),
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live game not found")
    return game


@router.get("/{group_id}/live-games/{live_game_id}", response_model=LiveGameOut)
async def get_live_game(
    group_id: uuid.UUID,
    live_game_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> LiveGameOut:
    game = await _get_live_game_owned(session, group_id, live_game_id, current)
    return _live_game_to_out(game)


@router.patch("/{group_id}/live-games/{live_game_id}", response_model=LiveGameOut)
async def patch_live_game(
    group_id: uuid.UUID,
    live_game_id: uuid.UUID,
    body: LiveGamePatch,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> LiveGameOut:
    game = await _get_live_game_owned(session, group_id, live_game_id, current)
    by_client = {p.client_player_id: p for p in game.players}

    seen_patch_ids: set[str] = set()
    for row in body.players:
        if row.client_player_id in seen_patch_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate client_player_id in players",
            )
        seen_patch_ids.add(row.client_player_id)

    for row in body.players:
        lp = by_client.get(row.client_player_id)
        if lp is None:
            dn = (row.display_name or "").strip()
            if not dn:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "display_name is required when adding a new player "
                        f"(client_player_id={row.client_player_id})"
                    ),
                )
            em: str | None = row.email
            lp = LiveGamePlayer(
                live_game_id=game.id,
                client_player_id=row.client_player_id,
                display_name=dn[:200],
                email=em,
                buy_in_coins=0,
            )
            session.add(lp)
            by_client[row.client_player_id] = lp
        else:
            if row.display_name and (row.display_name or "").strip():
                lp.display_name = str(row.display_name).strip()[:200]
            if row.email is not None:
                raw_e = str(row.email).strip() if row.email else ""
                lp.email = normalize_email(raw_e) if raw_e else None

    await session.flush()

    known_ids = set(by_client.keys())
    _validate_patch_events(body.events, known_ids)

    now = datetime.now(timezone.utc)
    for ev in body.events:
        session.add(
            LiveGameBuyInEvent(
                live_game_id=game.id,
                event_kind=ev.event_kind,
                target_client_player_id=ev.target_client_player_id,
                coins=ev.coins,
                from_client_player_id=ev.from_client_player_id,
            )
        )
    await session.flush()
    pl_res = await session.execute(
        select(LiveGamePlayer).where(LiveGamePlayer.live_game_id == game.id),
    )
    all_players = list(pl_res.scalars().all())
    await _sync_player_buy_ins_from_db_events(session, game.id, all_players)
    for p in all_players:
        if p.buy_in_coins < 0:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Buy-in ledger would go negative for at least one player "
                    f"(e.g. client_player_id={p.client_player_id}). Check return/transfer amounts."
                ),
            )

    game.updated_at = now
    grp = await session.get(PlayerGroup, group_id)
    if grp is not None:
        grp.updated_at = now
    await session.commit()

    result = await session.execute(
        select(LiveGame)
        .options(
            selectinload(LiveGame.players),
            selectinload(LiveGame.buy_in_events),
        )
        .where(LiveGame.id == game.id),
    )
    loaded = result.scalar_one()
    return _live_game_to_out(loaded)


@router.delete("/{group_id}/live-games/{live_game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_game(
    group_id: uuid.UUID,
    live_game_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> None:
    game = await _get_live_game_owned(session, group_id, live_game_id, current)
    now = datetime.now(timezone.utc)
    gid = game.group_id
    await session.delete(game)
    grp = await session.get(PlayerGroup, gid)
    if grp is not None:
        grp.updated_at = now
    await session.commit()