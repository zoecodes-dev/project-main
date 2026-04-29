FROM python:3.11-slim

WORKDIR /app

# MySQL 클라이언트 라이브러리 설치
# 파이썬(pymysql 등)에서 MySQL에 접속하기 위해 리눅스 OS 수준에서 필요한 패키지들
RUN apt-get update && apt-get install -y --no-install-recommends\
    default-libmysqlclient-dev \
    gcc \
    pkg-config \
 && rm -rf /var/lib/apt/lists/*

# 레이어 캐싱 원리를 위한 핵심!
# 소스코드 전체를 복사하기 전에 requirements.txt만 먼저 복사해.
# 이렇게 하면 패키지 목록이 안 바뀌었을 때 pip install 단계가 '캐시 히트'돼서 빌드가 엄청 빨라짐
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 패키지 설치가 끝난 후 나머지 전체 코드를 복사
COPY . .

# 개발 모드: uvicorn으로 FastAPI 서버 띄우기
# --reload 옵션 덕분에 코드를 수정하고 저장만 해도 서버가 알아서 재시작돼
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

