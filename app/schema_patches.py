"""Lightweight ALTERs for existing DBs (SQLAlchemy create_all does not add columns)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings


async def apply_postgres_schema_patches(conn: AsyncConnection) -> None:
    if "postgresql" not in settings.database_url.lower():
        return
    await conn.execute(
        text("ALTER TABLE users ADD COLUMN IF NOT EXISTS upi_id VARCHAR(100)"),
    )
    await conn.execute(
        text(
            "ALTER TABLE game_player_results "
            "ADD COLUMN IF NOT EXISTS stake_lent_coins INTEGER NOT NULL DEFAULT 0"
        ),
    )
    await conn.execute(
        text(
            "ALTER TABLE game_player_results "
            "ADD COLUMN IF NOT EXISTS stake_borrowed_coins INTEGER NOT NULL DEFAULT 0"
        ),
    )
