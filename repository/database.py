from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

from config.settings import settings
from repository.models import Base

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    """테이블 생성 (없으면 생성, 있으면 유지)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_db()


async def migrate_db() -> None:
    """스키마 변경 사항을 기존 DB에 반영"""
    migrations = [
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS target_price FLOAT",
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS trail_pct FLOAT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS chart_verdict VARCHAR(10)",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS chart_confidence FLOAT",
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


