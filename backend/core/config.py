from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 프로젝트 기본 설정
    PROJECT_NAME: str = "KIRA Compliance Intelligence Platform"
    
    # 데이터베이스 설정 (기본값을 지웠으므로 .env에 없으면 에러 발생!)
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    DATABASE_URL: str
    
    # Redis 설정
    REDIS_URL: str
    
    KIRA_EVENT_CHANNEL: str

    # JWT 인증 설정 (기본값을 지워서 강력하게 강제)
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # E2(데모 축소): 협력사 상세 모달 탭 노출 정책.
    #   True면 핵심 3탭(detail/factories/risk)만 노출하고 esg/training/reliability는 숨긴다.
    #   False(.env에서 SUPPLIER_DEMO_MODE=false)면 7탭 전체가 다시 살아난다 — 가역 토글.
    SUPPLIER_DEMO_MODE: bool = True

    # .env 파일을 자동으로 읽어오도록 설정
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        case_sensitive=True,
        extra="ignore"
    )

# 싱글톤 패턴
config = Settings()