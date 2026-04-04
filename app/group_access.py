import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.email_norm import normalize_email
from app.models import GroupMember, PlayerGroup, User
from app.schemas import GroupOut


def _legacy_member_strings(group: PlayerGroup) -> list[str]:
    raw = group.members
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x).strip()]


def member_emails_for_api(group: PlayerGroup) -> list[str]:
    rows = list(group.member_rows)
    if rows:
        owners = sorted([r.email for r in rows if r.role == "owner"])
        members = sorted([r.email for r in rows if r.role == "member"])
        ordered: list[str] = []
        for e in owners:
            if e not in ordered:
                ordered.append(e)
        for e in members:
            if e not in ordered:
                ordered.append(e)
        return ordered
    return _legacy_member_strings(group)


def my_role_for_group(group: PlayerGroup, user: User) -> str:
    if group.owner_id == user.id:
        return "owner"
    me = normalize_email(user.email)
    for r in group.member_rows:
        if r.email == me and r.role == "member":
            return "member"
    return "member"


def can_access_group(group: PlayerGroup, user: User) -> bool:
    if group.owner_id == user.id:
        return True
    me = normalize_email(user.email)
    for r in group.member_rows:
        if r.email == me:
            return True
    legacy = [normalize_email(x) for x in _legacy_member_strings(group)]
    return me in legacy


def group_to_out(group: PlayerGroup, user: User) -> GroupOut:
    role = my_role_for_group(group, user)
    return GroupOut(
        id=group.id,
        name=group.name,
        members=member_emails_for_api(group),
        my_role=role,  # type: ignore[arg-type]
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def sync_members_json(session: AsyncSession, group: PlayerGroup) -> None:
    group.members = member_emails_for_api(group)


async def link_group_members_users(session: AsyncSession, group_id: uuid.UUID) -> None:
    result = await session.execute(select(GroupMember).where(GroupMember.group_id == group_id))
    rows = list(result.scalars().all())
    if not rows:
        return
    emails = {r.email for r in rows}
    if not emails:
        return
    res_users = await session.execute(select(User).where(User.email.in_(emails)))
    users = list(res_users.scalars().all())
    by_email = {normalize_email(u.email): u for u in users}
    for row in rows:
        u = by_email.get(row.email)
        row.user_id = u.id if u else None


async def replace_member_rows(
    session: AsyncSession,
    group: PlayerGroup,
    owner: User,
    invite_emails: list[str],
) -> None:
    await session.execute(delete(GroupMember).where(GroupMember.group_id == group.id))
    await session.flush()
    owner_email = normalize_email(owner.email)
    session.add(
        GroupMember(
            group_id=group.id,
            email=owner_email,
            user_id=owner.id,
            role="owner",
        )
    )
    seen = {owner_email}
    for raw in invite_emails:
        e = normalize_email(raw)
        if e in seen:
            continue
        seen.add(e)
        session.add(
            GroupMember(
                group_id=group.id,
                email=e,
                user_id=None,
                role="member",
            )
        )
    await session.flush()
    await link_group_members_users(session, group.id)
    await session.refresh(group, attribute_names=["member_rows"])
    group.members = member_emails_for_api(group)


async def get_group_eager(
    session: AsyncSession,
    group_id: uuid.UUID,
) -> PlayerGroup | None:
    result = await session.execute(
        select(PlayerGroup)
        .options(selectinload(PlayerGroup.member_rows))
        .where(PlayerGroup.id == group_id)
    )
    return result.scalar_one_or_none()
