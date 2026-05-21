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

# 개발 모드: backend 루트의 main.py를 바라봄. --reload로 코드 수정 시 자동 재시작
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
