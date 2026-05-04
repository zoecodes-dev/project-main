from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from contextlib import asynccontextmanager
from database import get_db, engine, Base
from security import get_password_hash, verify_password, create_access_token
import models

# 서버 시작 시 테이블 자동 생성 관리
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        #models.py에 정의된 모든 테이블 설계도(Base.metadata)를 참조하여
        # DB에 테이블이 없다면 자동으로 생성합니다.
        await conn.run_sync(models.Base.metadata.create_all)
    yield
    await engine.dispose()

app = FastAPI(title="ESG Supply Chain Management API", lifespan=lifespan)

# [User] 회원가입: 입주민 등록
@app.post("/register")
async def register(username: str, password: str, db: AsyncSession = Depends(get_db)):
    # 보안을 위해 입력받은 비번을 해싱(암호화) 처리합니다.
    hashed_pwd = get_password_hash(password)
    # 암호화된 비번을 포함한 사용자 객체를 생성하여 세션에 추가합니다.
    new_user = models.User(username=username, pwd=hashed_pwd)
    db.add(new_user)
    # 비동기로 DB에 최종 확정(Commit)합니다.
    await db.commit()
    return {"message": "회원가입 성공"}

# [Auth] 로그인: 열쇠(JWT) 발급
@app.post("/login")
async def login(username: str, password: str, db: AsyncSession = Depends(get_db)):
    # 입력받은 아이디가 DB에 있는지 비동기로 조회합니다.
    result = await db.execute(select(models.User).filter(models.User.username == username))
    user = result.scalars().first()
    
    # 사용자가 없거나, 입력한 비번과 DB의 해시값이 일치하지 않으면 401 에러를 발생시킵니다.
    if not user or not verify_password(password, user.pwd):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호 오류")
    
    # 인증 성공 시, 사용자명을 담은 JWT 액세스 토큰을 생성하여 반환합니다.
    token = create_access_token(data={"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}

# [Post] 협력사 추가: 실제 데이터 활동
@app.post("/suppliers")
async def add_supplier(name: str, country: str, db: AsyncSession = Depends(get_db)):
    # 새로운 협력사 데이터를 생성하여 DB에 등록합니다.
    supplier = models.Supplier(name=name, country=country)
    db.add(supplier)
    await db.commit()
    # 저장된 데이터의 ID 등 최신 상태를 다시 불러와 확인합니다.
    await db.refresh(supplier)
    return supplier

# [Post] 협력사 전체 조회
@app.get("/suppliers")
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    # 등록된 모든 협력사 목록을 비동기로 가져옵니다.
    result = await db.execute(select(models.Supplier))
    return result.scalars().all()

'''
SQLAlchemy (Async Version)
'테이블/행'을 파이썬의 '클래스/객체'로 1:1 매칭시켜 주는 것은 동일하지만,
비동기 방식에서는 'await'를 통해 DB 응답을 기다리는 동안 서버가 쉬지 않게 합니다.

1. Engine (엔진): 번역기 & 통신선 관리
  - 비동기 엔진(create_async_engine)을 사용하여 'aiomysql' 드라이버로 DB와 소통합니다.
  - 여러 요청이 동시에 들어와도 통신선(Connection Pool)을 효율적으로 사용하여 서버가 멈추지 않습니다.

2. Session (세션): 비동기 데이터 작업장
  - AsyncSession을 사용하여 모든 DB 조작을 비동기로 수행합니다.
  - db.execute(select(...)): SQL을 실행하고 결과를 기다립니다.
  - await db.commit(): 세션에 쌓인 작업들을 DB에 확정 짓는 순간이며, 비동기로 처리되어 효율적입니다.

[왜 비동기로 수정했는가?]
우리가 구현한 Auth(인증)와 User(사용자) 기능이 이미 비동기(await) 기반으로 설계되었습니다.
메인 엔진과 API 로직을 비동기로 일치시켜야만 전체 시스템이 에러 없이 고성능으로 작동할 수 있습니다.
'''