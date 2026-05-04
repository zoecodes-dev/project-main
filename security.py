from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
import os

# 비밀번호 암호화 설정
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 설정값 (환경변수에서 가져오기)
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# 1. 비밀번호 암호화 및 검증
def get_password_hash(password: str):
    return pwd_context.hash(password)
"""
사용자가 입력한 평문 비밀번호를 암호화(해싱)합니다.
- bcrypt 알고리즘을 사용하여 단방향 암호화를 수행합니다.
- DB에는 이 해시값만 저장되므로, 데이터베이스가 유출되어도 실제 비번을 알 수 없습니다.
"""
def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)
"""
로그인 시 입력한 비번과 DB에 저장된 해시값을 비교합니다.
- 평문 비번을 다시 해싱하여 저장된 해시값과 일치하는지 검증합니다.
- 일치하면 True, 다르면 False를 반환하여 인증 여부를 결정합니다.
"""
# 2. Access Token 생성 
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    사용자 정보(payload)를 담은 JWT 토큰을 생성합니다.
    """
    to_encode = data.copy()
    # 토큰의 유효 기간(만료 시간)을 설정합니다.
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # 기본값으로 설정된 시간(예: 30분)만큼 유효하게 설정합니다.
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # 토큰 내부에 만료 시간('exp') 정보를 주입합니다.
    to_encode.update({"exp": expire})
    
    # 설정된 SECRET_KEY와 HS256 알고리즘을 사용하여 최종적으로 토큰을 서명/인코딩합니다.
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# 3. 토큰 검증 기능 (추가됨!)
# 사용자가 가져온 열쇠가 진짜인지, 유효기간이 지나지 않았는지 확인합니다.
def verify_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload  # 성공하면 토큰 안의 내용(유저 정보 등)을 반환
    except JWTError:
        return None     # 실패하면 None 반환