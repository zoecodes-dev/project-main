from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from database import Base

# [User] 입주민: 회원 정보를 담는 테이블
# 로그인을 하려면 이 설계도가 있어야 DB에 아이디와 비밀번호를 저장할 수 있습니다.
class User(Base):
    __tablename__ = "users" # 실제 DB에 생성될 테이블 이름

    id = Column(Integer, primary_key=True, index=True)
    # unique=True: 똑같은 아이디로 중복 가입하는 것을 방지합니다.
    username = Column(String(50), unique=True, index=True, nullable=False)
    # pwd: 암호화된(Hash) 비밀번호가 저장되는 곳입니다. 
    # 보안을 위해 길이를 200으로 넉넉하게 설정했습니다.
    pwd = Column(String(200), nullable=False)
    # 가입일자 자동 기록
    created_at = Column(DateTime, server_default=func.now())


# [Post/Supplier] 활동: 협력사 정보를 담는 테이블
class Supplier(Base):
    __tablename__ = "suppliers" 

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)       # 협력사명
    country = Column(String(50), nullable=False)      # 국가
    created_at = Column(DateTime, server_default=func.now())

'''
[ Model 설계 설명 ]
1. User 모델: 회원가입 및 JWT 인증의 바탕이 되는 테이블입니다. 
   비밀번호를 생으로 저장하지 않고, 암호화된 문자열을 수용할 수 있도록 설계
2. Supplier 모델: 실제 서비스의 핵심 데이터인 협력사 정보를 관리합니다.
'''