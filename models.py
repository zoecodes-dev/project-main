from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from database import Base

# 우리가 만들 '협력사' 테이블의 설계도(ORM 모델)
class Supplier(Base):
    __tablename__ = "suppliers" # 실제 DB에 생성될 테이블 이름

    # index=True로 설정해두면 나중에 id로 데이터를 검색할 때 훨씬 빠름
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)       # 협력사명 (빈 값 안 됨)
    country = Column(String(50), nullable=False)      # 국가 (빈 값 안 됨)
    # 데이터가 추가될 때의 시간을 자동으로 기록해주는 컬럼
    created_at = Column(DateTime, server_default=func.now())