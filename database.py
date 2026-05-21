import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")

# 비동기 DB 엔진 생성 (echo=True로 설정 시 쿼리 로그 확인 가능)
engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session