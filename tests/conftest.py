# 프로젝트 모듈 임포트 전에 필수 env vars 설정 (settings = Settings() 실패 방지)
import os
os.environ.setdefault("KIS_APP_KEY", "test_key")
os.environ.setdefault("KIS_APP_SECRET", "test_secret")
os.environ.setdefault("KIS_ACCOUNT_NO", "12345678-01")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from repository.models import Base

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    """매 테스트마다 새 인메모리 SQLite DB와 세션을 생성."""
    engine = create_async_engine(_TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
