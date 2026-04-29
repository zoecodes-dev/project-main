from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# .env나 docker-compose 환경변수에서 주입받은 DB 연결 주소
DATABASE_URL = os.getenv("DATABASE_URL")

# SQLAlchemy 엔진 생성. DB랑 직접 통신하는 핵심 객체
engine = create_engine(DATABASE_URL)

# DB 세션 팩토리. 여기서 만들어진 세션으로 쿼리를 날림
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 우리가 만들 테이블(Model)들의 기본 클래스가 될 Base
Base = declarative_base()

# FastAPI의 Depends()와 함께 쓰는 DB 세션 관리 함수
# 요청이 들어올 때마다 세션을 열고, 끝나면(혹은 에러가 나도) 안전하게 닫아주는 역할을 함
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
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
'''
