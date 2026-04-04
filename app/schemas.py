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
    buy_in_coins: int = Field(ge=0)
    remaining_coins: int = Field(ge=0)

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
