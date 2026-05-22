"""
infrastructure/security.py  (담당: 팀원 B / 공통)

인증·인가 공통 모듈. 도메인이 아니라 횡단 관심사(cross-cutting)이므로
infrastructure 계층에 둔다. (기존 flat 루트의 security.py에서 이동)

- 비밀번호 bcrypt 해싱/검증
- JWT Access Token 발급/검증
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from backend.core.config import config

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ----- 1. 비밀번호 암호화/검증 -----
def get_password_hash(password: str) -> str:
    """평문 비밀번호를 bcrypt로 단방향 해싱. DB에는 해시값만 저장."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """입력 비밀번호와 저장된 해시값 비교. 일치 시 True."""
    return pwd_context.verify(plain_password, hashed_password)


# ----- 2. Access Token 발급 -----
def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """payload에 만료시간(exp)을 주입해 JWT 발급."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    
    return jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)


# ----- 3. 토큰 검증 -----
def verify_access_token(token: str) -> Optional[dict]:
    """토큰 유효성·만료 검증. 성공 시 payload, 실패 시 None."""
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except JWTError:
        return None