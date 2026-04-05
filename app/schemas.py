import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

from app.email_norm import normalize_email


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    member_emails: list[EmailStr] = Field(
        default_factory=list,
        validation_alias=AliasChoices("member_emails", "members"),
    )

    @field_validator("member_emails", mode="before")
    @classmethod
    def _norm_emails(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        return [normalize_email(str(x)) for x in v]


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    member_emails: list[EmailStr] | None = Field(
        default=None,
        validation_alias=AliasChoices("member_emails", "members"),
    )

    @field_validator("member_emails", mode="before")
    @classmethod
    def _norm_emails(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, list):
            return v
        return [normalize_email(str(x)) for x in v]


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    members: list[str]
    my_role: Literal["owner", "member"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GameLineIn(BaseModel):
    email: EmailStr | None = None
    display_name: str = Field(min_length=1, max_length=200)
    # Can be negative when repeated player→player loans leave a net creditor on the book.
    buy_in_coins: int = Field(ge=-1_000_000, le=1_000_000)
    remaining_coins: int = Field(ge=0)
    stake_lent_coins: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("stake_lent_coins", "stakeLentCoins"),
    )
    stake_borrowed_coins: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("stake_borrowed_coins", "stakeBorrowedCoins"),
    )

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return normalize_email(str(v))


class GameCreate(BaseModel):
    rupees_per_coin: float = Field(gt=0)
    finished_at: datetime | None = None
    lines: list[GameLineIn] = Field(min_length=2)


class GameLineOut(BaseModel):
    email: str | None
    display_name: str
    buy_in_coins: int
    remaining_coins: int
    stake_lent_coins: int = 0
    stake_borrowed_coins: int = 0
    profit_rupees: float

    model_config = {"from_attributes": True}


class GameOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    rupees_per_coin: float
    finished_at: datetime
    created_at: datetime
    lines: list[GameLineOut]

    model_config = {"from_attributes": True}


class GameSummaryOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    group_name: str
    rupees_per_coin: float
    finished_at: datetime
    my_profit_rupees: float | None


class GroupGamesSummaryOut(BaseModel):
    group_id: uuid.UUID
    group_name: str
    games_count: int
    net_profit_rupees: float


class DashboardOut(BaseModel):
    total_games: int
    net_profit_rupees: float
    by_group: list[GroupGamesSummaryOut]
    recent_games: list[GameSummaryOut]


_upi_re = re.compile(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z0-9.\-]{2,32}$")


class ProfileUpdate(BaseModel):
    """Update current user profile fields."""

    upi_id: str | None = Field(default=None, max_length=100)

    @field_validator("upi_id", mode="before")
    @classmethod
    def _normalize_upi(cls, v: object) -> object:
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return None
        if not _upi_re.match(s):
            raise ValueError("Invalid UPI ID (expected handle@provider, e.g. name@okaxis)")
        return s


class MemberUpiEntry(BaseModel):
    upi_id: str
    display_name: str | None


class GroupMemberUpiMapOut(BaseModel):
    members: dict[str, MemberUpiEntry | None]


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str | None
    upi_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Live games (in-progress tables, synced from the app) ---

BuyInEventKind = Literal["initial", "bank", "player_transfer"]
InitialBuyInSource = Literal["bank", "player_transfer"]


class LiveGamePlayerStartIn(BaseModel):
    client_player_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    initial_buy_in_source: InitialBuyInSource = "bank"
    from_client_player_id: str | None = Field(default=None, max_length=80)

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return normalize_email(str(v))


class LiveGameCreate(BaseModel):
    rupees_per_coin: float = Field(gt=0)
    initial_buy_in_coins: int = Field(gt=0)
    players: list[LiveGamePlayerStartIn] = Field(min_length=2)


class LiveGamePlayerStateIn(BaseModel):
    client_player_id: str = Field(min_length=1, max_length=80)
    """Required in PATCH when introducing a new [client_player_id] mid-game."""
    display_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    # Ignored: server derives buy-in from [live_game_buy_in_events] only.
    buy_in_coins: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_display_patch(cls, v: object) -> object:
        if v is None or v == "":
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email_patch_player(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return normalize_email(str(v))


class LiveGameBuyInEventIn(BaseModel):
    event_kind: BuyInEventKind
    target_client_player_id: str = Field(min_length=1, max_length=80)
    coins: int = Field(gt=0)
    from_client_player_id: str | None = Field(default=None, max_length=80)


class LiveGamePatch(BaseModel):
    players: list[LiveGamePlayerStateIn] = Field(min_length=1)
    events: list[LiveGameBuyInEventIn] = Field(default_factory=list)


class LiveGamePlayerOut(BaseModel):
    client_player_id: str
    email: str | None
    display_name: str
    buy_in_coins: int

    model_config = {"from_attributes": True}


class LiveGameBuyInEventOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    event_kind: str
    target_client_player_id: str
    coins: int
    from_client_player_id: str | None

    model_config = {"from_attributes": True}


class LiveGameOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    rupees_per_coin: float
    initial_buy_in_coins: int
    created_at: datetime
    updated_at: datetime
    players: list[LiveGamePlayerOut]
    buy_in_events: list[LiveGameBuyInEventOut]

    model_config = {"from_attributes": True}


class LiveGameSummaryOut(BaseModel):
    """Row for Play tab: other devices / same account can see tables in progress."""

    id: uuid.UUID
    group_id: uuid.UUID
    group_name: str
    rupees_per_coin: float
    initial_buy_in_coins: int
    player_count: int
    total_buy_in_coins: int
    updated_at: datetime
    created_at: datetime
