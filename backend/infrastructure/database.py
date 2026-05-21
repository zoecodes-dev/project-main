"""
infrastructure/database.py  (담당: 팀원 B)

SQLAlchemy async 세션 팩토리.
PostGIS, pgvector 확장이 켜진 상태인지 startup 시 검증.

스펙 1-1 인터페이스:
    async def get_db() -> AsyncSession        # FastAPI Depends용
    async def verify_extensions() -> None      # startup_event에서 호출
"""
import os
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://kira_admin:kira_secure_pass@db:5432/kira_db",
)

# echo=True 설정 시 쿼리 로그 확인 가능
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends용 세션 의존성."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def verify_extensions() -> None:
    """
    startup 시 PostGIS / pgvector 확장이 적재됐는지 검증.
    누락 시 RuntimeError를 던져 부팅을 막는다.
    """
    required = {"postgis", "vector"}
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT extname FROM pg_extension")
        )
        installed = {row[0] for row in result}

    missing = required - installed
    if missing:
        raise RuntimeError(
            f"필수 PostgreSQL 확장 누락: {missing}. init.sql을 확인하세요."
        )

    print(f"[DB] PostGIS OK, pgvector OK (installed: {sorted(installed & required)})")
