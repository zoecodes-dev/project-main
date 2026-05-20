from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
import os
from typing import AsyncGenerator

# .env에서 가져온 DB 주소 (비동기 통신을 위해 mysql+aiomysql 사용)
DATABASE_URL = os.getenv("DATABASE_URL")

# 1. Engine (엔진): 번역기 & 통신선 관리
# 비동기 엔진을 사용하여 DB 응답을 기다리는 동안 서버가 쉬지 않게 합니다.
# echo=True : 내가 짠 비동기 로직이 실제로 DB에 언제, 어떻게 요청을 보내는지 흐름을 파악하기 위해
engine = create_async_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

# 2. Session (세션): 실제 데이터 작업장
# 비동기 세션(AsyncSession)을 사용하여 데이터 조작을 수행합니다.
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

Base = declarative_base()

# 의존성 주입을 위한 세션 제공 함수 (요청마다 세션을 열고 닫음)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
        
'''
SQLAlchemy
'테이블/행'을 파이썬의 '클래스/객체'로 1:1 매칭시켜 줌 
SELECT * FROM suppliers 같은 SQL 문자열을 
db.query(Supplier).all()처럼 파이썬 코드로 DB를 조작할 수 있게 해주는 핵심 도구

이 과정을 가능하게 하는 두 가지 핵심 객체가 바로 Engine과 Session입니다.

1. Engine (엔진): 번역기 & 통신선 관리
  DB와 소통하기 위한 가장 밑바탕이 되는 뼈대.
  - 번역: 파이썬 코드를 DB(MySQL)가 알아들을 수 있는 SQL 문법으로 번역해 줌.
  - 통신선 유지 (Connection Pool): 요청마다 DB와 새로 연결하면 서버가 느려짐. 그래서 엔진이 미리 DB와 연결된 '통신선'을 여러 개 만들어두고 필요할 때마다 빌려줌.

2. Session (세션): 실제 데이터 작업장
  엔진이 뚫어준 통신선을 빌려와서, 실제로 데이터를 조작하는 공간.
  - 임시 저장: db.add() 한다고 DB에 바로 저장 안 됨. 세션이라는 작업장에 '이거 추가할 예정'이라고 임시로 기록만 해둠.
  - 한 번에 확정 (Commit / Rollback): db.commit()을 호출하는 순간, 모아둔 작업을 DB로 한 번에 쏴서 저장(확정)함. 중간에 에러 나면 작업장 내역을 싹 지워버려서(rollback) DB를 안전하게 보호함.
[ 추가 학습: 비동기(Asynchronous) 방식의 이해 ]

FastAPI의 성능을 극대화하기 위해 기존의 동기 방식에서 비동기 방식으로 업그레이드했습니다.
핵심은 "기다리는 시간을 낭비하지 않는 것"입니다.

- 기존 동기 방식 드라이버(pymysql)를 비동기 전용(aiomysql)으로 교체하였습니다.

- 이유: FastAPI의 고성능 비동기 처리 기능을 100% 활용하고, 
  나중에 구현될 JWT 인증 로직과의 코드 일관성을 유지하기 위함입니다.

[ 비동기(Asynchronous) 방식의 핵심 이해 ]

1. 왜 비동기로 전환했는가?
   - 우리가 구현한 Auth(인증)와 User(사용자) 기능이 이미 비동기(await) 기반으로 설계되었습니다.
   - 엔진까지 비동기로 맞춰야 데이터가 꼬이지 않고 고성능으로 작동할 수 있습니다.

2. 동기 vs 비동기 차이
   - 동기: DB가 답장할 때까지 서버 전체가 '일시 정지' 상태로 기다립니다.
   - 비동기: DB에 요청을 던져놓고, 답장이 올 때까지 다른 사용자의 요청을 처리합니다.
   
   
'''
