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

    # AWS / 메일(SES) 설정
    #   MAIL_ENABLED=False 또는 MAIL_FROM 미설정이면 실제 발송 없이 no-op(로그만).
    #   → SES 발신 identity 검증 전/로컬에서도 안전. 검증 후 .env에서 켠다.
    #   실행 자격증명: EC2는 IAM Role, 로컬은 ~/.aws (INFRA-5). boto3 기본 체인 사용.
    AWS_REGION: str = "ap-northeast-2"
    MAIL_ENABLED: bool = False
    MAIL_FROM: str = ""                       # SES 검증된 발신 주소 (예: no-reply@도메인)
    FRONTEND_BASE_URL: str = "http://localhost:3000"   # 초대/가입 URL 조립 기준

    # .env 파일을 자동으로 읽어오도록 설정
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        case_sensitive=True,
        extra="ignore"
    )

# 싱글톤 패턴
config = Settings()