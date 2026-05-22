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

    # .env 파일을 자동으로 읽어오도록 설정
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        case_sensitive=True,
        extra="ignore"
    )

# 싱글톤 패턴
config = Settings()