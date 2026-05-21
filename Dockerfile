# 파이썬 3.11 슬림 이미지를 기반으로 사용
FROM python:3.11-slim

# 컨테이너 내 작업 디렉토리 설정
WORKDIR /app

# PostgreSQL 및 필수 빌드 종속성 설치 (MySQL 대체)
# 파이썬(asyncpg, psycopg2 등)에서 PostgreSQL에 접속하기 위해 리눅스 OS 수준에서 필요한 패키지들
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 레이어 캐싱 원리를 위한 핵심!
# 소스코드 전체를 복사하기 전에 requirements.txt만 먼저 복사
# 이렇게 하면 패키지 목록이 안 바뀌었을 때 pip install 단계가 '캐시 히트'돼서 빌드가 엄청 빨라짐
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 패키지 설치가 끝난 후 나머지 전체 코드를 복사
COPY . .

# 개발 모드: uvicorn으로 FastAPI 서버 띄우기 (경로 주의!)
# backend 폴더 안의 main.py를 바라보도록 지정
# --reload 옵션 덕분에 코드를 수정하고 저장만 해도 서버가 알아서 재시작
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]