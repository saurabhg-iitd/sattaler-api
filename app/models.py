import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upi_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    groups: Mapped[list["PlayerGroup"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class PlayerGroup(Base):
    __tablename__ = "player_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Kept for backward compatibility with existing DB rows; synced from group_members on write.
    members: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship(back_populates="groups")
    member_rows: Mapped[list["GroupMember"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    games: Mapped[list["Game"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    live_games: Mapped[list["LiveGame"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "email", name="uq_group_member_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("player_groups.id", ondelete="CASCADE"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "owner" | "member"

    group: Mapped["PlayerGroup"] = relationship(back_populates="member_rows")


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("player_groups.id", ondelete="CASCADE"),
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rupees_per_coin: Mapped[float] = mapped_column()
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped["PlayerGroup"] = relationship(back_populates="games")
    lines: Mapped[list["GamePlayerResult"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )


class GamePlayerResult(Base):
    __tablename__ = "game_player_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    buy_in_coins: Mapped[int] = mapped_column()
    remaining_coins: Mapped[int] = mapped_column()
    # Cumulative stake moved via “player → player” (chips lent from the pot); used for session P&L.
    stake_lent_coins: Mapped[int] = mapped_column(server_default=text("0"))
    stake_borrowed_coins: Mapped[int] = mapped_column(server_default=text("0"))

    game: Mapped["Game"] = relationship(back_populates="lines")


class LiveGame(Base):
    """In-progress table synced from the app until finished (saved game) or abandoned."""

    __tablename__ = "live_games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("player_groups.id", ondelete="CASCADE"),
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    rupees_per_coin: Mapped[float] = mapped_column()
    initial_buy_in_coins: Mapped[int] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped["PlayerGroup"] = relationship(back_populates="live_games")
    players: Mapped[list["LiveGamePlayer"]] = relationship(
        back_populates="live_game",
        cascade="all, delete-orphan",
    )
    buy_in_events: Mapped[list["LiveGameBuyInEvent"]] = relationship(
        back_populates="live_game",
        cascade="all, delete-orphan",
    )


class LiveGamePlayer(Base):
    __tablename__ = "live_game_players"
    __table_args__ = (UniqueConstraint("live_game_id", "client_player_id", name="uq_live_game_client_player"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    live_game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("live_games.id", ondelete="CASCADE"),
        index=True,
    )
    client_player_id: Mapped[str] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    buy_in_coins: Mapped[int] = mapped_column()

    live_game: Mapped["LiveGame"] = relationship(back_populates="players")


class LiveGameBuyInEvent(Base):
    """Audit trail: initial stack, bank rebuy, or chip transfer between players."""

    __tablename__ = "live_game_buy_in_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    live_game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("live_games.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_kind: Mapped[str] = mapped_column(String(24))
    target_client_player_id: Mapped[str] = mapped_column(String(80))
    coins: Mapped[int] = mapped_column()
    from_client_player_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    live_game: Mapped["LiveGame"] = relationship(back_populates="buy_in_events")
