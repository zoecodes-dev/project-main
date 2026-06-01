# 파이썬 3.11 슬림 이미지 기반
FROM python:3.11-slim

WORKDIR /app

# PostgreSQL 클라이언트 빌드 종속성 (asyncpg/psycopg2용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 레이어 캐싱: requirements만 먼저 복사 → 패키지 미변경 시 캐시 히트
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 나머지 소스 전체 복사 (build context = backend/)
COPY . .

# 배포 모드: --reload 제거, worker 2개로 동시 요청 처리
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
