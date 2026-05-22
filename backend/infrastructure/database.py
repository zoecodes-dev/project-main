"""
infrastructure/database.py  (담당: 팀원 B)

SQLAlchemy async 세션 팩토리.
PostGIS, pgvector 확장이 켜진 상태인지 startup 시 검증.
"""
from typing import AsyncGenerator, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine, AsyncConnection
from sqlalchemy.orm import DeclarativeBase
from backend.core.config import config 

# echo=True 설정 시 쿼리 로그 확인 가능
engine = create_async_engine(config.DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends용 세션 의존성."""
    # async with가 알아서 close()를 호출하므로 try-finally 불필요
    async with AsyncSessionLocal() as session:
        yield session


async def verify_extensions(conn: Optional[AsyncConnection] = None) -> None:
    """
    startup 시 PostGIS / pgvector 확장이 적재됐는지 검증.
    누락 시 RuntimeError를 던져 부팅을 막는다.
    main.py의 lifespan에서 연결된 conn을 넘겨받아 사용할 수 있도록 수정.
    """
    required = {"postgis", "vector"}
    
    async def _check(connection: AsyncConnection):
        result = await connection.execute(
            text("SELECT extname FROM pg_extension")
        )
        installed = {row[0] for row in result}
        
        missing = required - installed
        if missing:
            raise RuntimeError(
                f"필수 PostgreSQL 확장 누락: {missing}. init.sql을 확인하세요."
            )
        print(f"✅ [DB] PostGIS & pgvector OK (installed: {sorted(installed & required)})")

    # 외부에서 커넥션을 넘겨주면 그것을 사용, 아니면 자체적으로 생성
    if conn:
        await _check(conn)
    else:
        async with engine.connect() as new_conn:
            await _check(new_conn)