# """Lightweight ALTERs for existing DBs (SQLAlchemy create_all does not add columns to tables that already exist)."""

# from sqlalchemy import text
# from sqlalchemy.ext.asyncio import AsyncConnection

# from app.config import settings


# async def apply_postgres_schema_patches(conn: AsyncConnection) -> None:
#     if "postgresql" not in settings.database_url.lower():
#         return
#     # Auth 500 fix: User model gained upi_id after initial deploy.
#     await conn.execute(
#         text("ALTER TABLE users ADD COLUMN IF NOT EXISTS upi_id VARCHAR(100)"),
#     )
