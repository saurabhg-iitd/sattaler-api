import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.deps import get_current_user
from app.email_norm import normalize_email
from app.group_access import (
    accessible_groups_where_clause,
    can_access_group,
    get_group_eager,
    group_to_out,
    member_emails_for_api,
    replace_member_rows,
)
from app.models import PlayerGroup, User
from app.schemas import GroupCreate, GroupMemberUpiMapOut, GroupOut, GroupUpdate, MemberUpiEntry

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
async def list_groups(
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> list[GroupOut]:
    result = await session.execute(
        select(PlayerGroup)
        .options(selectinload(PlayerGroup.member_rows))
        .where(accessible_groups_where_clause(current))
        .order_by(PlayerGroup.updated_at.desc())
    )
    groups = list(result.scalars().unique().all())
    return [group_to_out(g, current) for g in groups]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GroupOut:
    now = datetime.now(timezone.utc)
    group = PlayerGroup(
        owner_id=current.id,
        name=body.name,
        members=[],
        updated_at=now,
    )
    session.add(group)
    await session.flush()
    invites = [normalize_email(str(e)) for e in body.member_emails]
    invites = [e for e in invites if e != normalize_email(current.email)]
    await replace_member_rows(session, group, current, invites)
    group.updated_at = now
    await session.commit()
    loaded = await get_group_eager(session, group.id)
    assert loaded is not None
    return group_to_out(loaded, current)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GroupOut:
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group_to_out(group, current)


@router.get("/{group_id}/member-upi", response_model=GroupMemberUpiMapOut)
async def group_member_upi_map(
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GroupMemberUpiMapOut:
    """UPI IDs for group roster (only members who saved one on their profile)."""
    group = await get_group_eager(session, group_id)
    if group is None or not can_access_group(group, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    roster = member_emails_for_api(group)
    if not roster:
        return GroupMemberUpiMapOut(members={})
    result = await session.execute(select(User).where(func.lower(User.email).in_(roster)))
    by_email: dict[str, User] = {}
    for u in result.scalars().all():
        by_email[normalize_email(u.email)] = u
    members: dict[str, MemberUpiEntry | None] = {}
    for em in roster:
        u = by_email.get(em)
        if u is None:
            members[em] = None
            continue
        raw = (u.upi_id or "").strip()
        if not raw:
            members[em] = None
        else:
            members[em] = MemberUpiEntry(upi_id=raw, display_name=u.display_name)
    return GroupMemberUpiMapOut(members=members)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: uuid.UUID,
    body: GroupUpdate,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> GroupOut:
    group = await get_group_eager(session, group_id)
    if group is None or group.owner_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if body.name is not None:
        group.name = body.name
    if body.member_emails is not None:
        invites = [normalize_email(str(e)) for e in body.member_emails]
        invites = [e for e in invites if e != normalize_email(current.email)]
        await replace_member_rows(session, group, current, invites)
    group.updated_at = datetime.now(timezone.utc)
    group.members = member_emails_for_api(group)
    await session.commit()
    loaded = await get_group_eager(session, group_id)
    assert loaded is not None
    return group_to_out(loaded, current)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: User = Depends(get_current_user),
) -> Response:
    group = await get_group_eager(session, group_id)
    if group is None or group.owner_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    await session.delete(group)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
